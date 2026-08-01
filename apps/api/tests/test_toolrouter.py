"""Tool routing and the zero-model guarantee for deterministic tools."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from tests.conftest import parse_sse

from kimi.services.toolrouter import ResearchMode, route

# ---- routing --------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["/calc 25*4", "25*4", "(2+3)*4", "what is 25*4", "calculate 100/4", "  2**8  "],
)
def test_arithmetic_routes_to_the_deterministic_calculator(text: str) -> None:
    plan = route(text)
    assert plan.tool_id == "calculator"
    assert plan.deterministic is True


@pytest.mark.parametrize(
    "text",
    [
        "How do I open a file in Python?",
        "what is the current working directory",
        "show me the source code",
        "explain how web servers work",
        "ما معنى الوزراء في هذه الجملة",
    ],
)
def test_conversational_messages_do_not_trigger_a_search(text: str) -> None:
    """AUDIT §5: unbounded substring matching made these fire a live search."""
    assert route(text).tool_id is None


@pytest.mark.parametrize(
    "text",
    ["latest news on Libya", "Libya news from the last 24 hours", "أخبار ليبيا اليوم"],
)
def test_news_phrasing_routes_to_news_search(text: str) -> None:
    plan = route(text)
    assert plan.tool_id == "news_search"
    assert plan.deterministic is False


def test_an_explicit_url_routes_to_read_article() -> None:
    plan = route("summarise https://example.com/a-story-2026 please")
    assert plan.tool_id == "read_article"
    assert plan.arguments["url"] == "https://example.com/a-story-2026"


def test_research_off_disables_search_but_not_the_calculator() -> None:
    assert route("latest news on Libya", research=ResearchMode.OFF).tool_id is None
    assert route("25*4", research=ResearchMode.OFF).tool_id == "calculator"


def test_research_always_searches_a_plain_question() -> None:
    plan = route("who runs Libya's central bank", research=ResearchMode.ALWAYS)
    assert plan.tool_id == "web_search"


def test_slash_commands_are_honoured() -> None:
    assert route("/search Libya oil").tool_id in ("web_search", "news_search")
    assert route("/open https://example.com/x").tool_id == "open_public_url"


def test_bare_filename_is_not_mistaken_for_a_url() -> None:
    """AUDIT §5: BARE_DOMAIN_PATTERN made "app.py" fetch https://app.py."""
    assert route("what does app.py do?").tool_id is None


# ---- the zero-model guarantee, end to end --------------------------------


async def test_calculator_turn_makes_no_model_call(
    client: AsyncClient, conversation_id: str, provider
) -> None:
    """Acceptance 8: deterministic tools perform zero model calls."""
    resp = await client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "content": "/calc 25*4"},
    )
    events = parse_sse(resp.text)

    # The provider double records every call it receives.
    assert provider.calls == [], "a deterministic tool must not call the model"

    done = next(d for e, d in events if e == "done")
    assert done["model_called"] is False
    assert done["usage"] is None
    assert done["finish_reason"] == "tool"

    text = "".join(d["text"] for e, d in events if e == "delta")
    assert "100" in text

    # Tool duration is reported separately from model timing.
    assert done["timing"]["tool_ms"] is not None
    assert done["timing"]["first_token_ms"] is None


async def test_calculator_result_is_persisted_with_its_tool_record(
    client: AsyncClient, conversation_id: str
) -> None:
    await client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "content": "/calc 12*12"},
    )
    detail = (await client.get(f"/api/conversations/{conversation_id}")).json()
    assistant = detail["messages"][-1]

    assert "144" in assistant["content"]
    assert assistant["model_id"] is None  # no model was involved
    assert assistant["tool"]["tool_id"] == "calculator"
    assert assistant["tool"]["status"] == "completed"
    assert assistant["tool"]["duration_ms"] >= 0


async def test_tool_lifecycle_events_are_streamed(
    client: AsyncClient, conversation_id: str
) -> None:
    resp = await client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "content": "/calc 7*6"},
    )
    tool_frames = [d for e, d in parse_sse(resp.text) if e == "tool"]

    assert tool_frames[0]["status"] == "running"
    assert tool_frames[-1]["status"] == "completed"
    assert tool_frames[-1]["tool_id"] == "calculator"
    assert tool_frames[-1]["renderer"] == "calculation"


async def test_a_failing_deterministic_tool_reports_cleanly(
    client: AsyncClient, conversation_id: str, provider
) -> None:
    resp = await client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "content": "/calc 1/0"},
    )
    events = parse_sse(resp.text)

    assert provider.calls == []
    error = next(d for e, d in events if e == "error")
    assert error["code"] == "division_by_zero"

    tool_frames = [d for e, d in events if e == "tool"]
    assert tool_frames[-1]["status"] == "failed"

    # No empty assistant card is left behind: the turn is closed.
    assert any(e == "done" for e, _ in events)


async def test_plain_chat_still_reaches_the_model(
    client: AsyncClient, conversation_id: str, provider
) -> None:
    resp = await client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "content": "Say hello"},
    )
    events = parse_sse(resp.text)
    assert len(provider.calls) == 1
    done = next(d for e, d in events if e == "done")
    assert done["model_called"] is True


async def test_research_off_keeps_a_news_question_as_plain_chat(
    client: AsyncClient, conversation_id: str, provider
) -> None:
    resp = await client.post(
        "/api/chat/stream",
        json={
            "conversation_id": conversation_id,
            "content": "latest news on Libya",
            "research": "off",
        },
    )
    events = parse_sse(resp.text)
    assert not any(e == "tool" for e, _ in events)
    assert len(provider.calls) == 1


async def test_tools_endpoint_advertises_the_registry(client: AsyncClient) -> None:
    body = (await client.get("/api/tools")).json()
    ids = {t["id"] for t in body["tools"]}
    assert {"calculator", "open_public_url", "read_article", "web_search", "news_search"} <= ids

    calc = next(t for t in body["tools"] if t["id"] == "calculator")
    assert calc["deterministic"] is True
    assert calc["requires_model_followup"] is False
    assert "properties" in calc["input_schema"]


async def test_tool_endpoint_runs_the_calculator_without_the_model(
    client: AsyncClient, provider
) -> None:
    resp = await client.post("/api/tools/calculator", json={"expression": "6*7"})
    body = resp.json()
    assert body["status"] == "completed"
    assert body["result"]["result"] == "42"
    assert body["model_called"] is False
    assert provider.calls == []

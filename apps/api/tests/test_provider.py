"""TokenRouter provider tests against a mocked HTTP transport."""

from __future__ import annotations

import httpx
import pytest
import respx

from kimi.errors import (
    ModelAuthError,
    ModelError,
    ModelRateLimitedError,
    ModelTimeoutError,
    UnsupportedCapabilityError,
)
from kimi.providers.base import Capability, ImagePart, Message, StreamDone, TextDelta
from kimi.providers.tokenrouter import TokenRouterProvider, model_info

BASE = "https://api.example.test/v1"
URL = f"{BASE}/chat/completions"


def sse(*frames: str) -> bytes:
    return "".join(f"data: {f}\n\n" for f in frames).encode()


def make(**kw: object) -> TokenRouterProvider:
    kw.setdefault("api_key", "test-key")
    kw.setdefault("base_url", BASE)
    return TokenRouterProvider(**kw)  # type: ignore[arg-type]


async def collect(provider: TokenRouterProvider, **kw: object) -> list:
    kw.setdefault("messages", [Message(role="user", content="hi")])
    kw.setdefault("model", "moonshotai/kimi-k3-free")
    return [e async for e in provider.stream_chat(**kw)]  # type: ignore[arg-type]


@respx.mock
async def test_parses_sse_deltas_and_usage() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            content=sse(
                '{"choices":[{"delta":{"content":"Hel"}}]}',
                '{"choices":[{"delta":{"content":"lo"}}]}',
                '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
                '{"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2,"total_tokens":9}}',
                "[DONE]",
            ),
        )
    )
    events = await collect(make())
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Hello"

    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.finish_reason == "stop"
    assert done.usage.total_tokens == 9
    assert done.timing.first_token_ms is not None
    assert done.timing.total_ms is not None


@respx.mock
async def test_malformed_chunk_is_skipped_not_fatal() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            content=sse(
                '{"choices":[{"delta":{"content":"a"}}]}',
                "{not json",
                '{"choices":[{"delta":{"content":"b"}}]}',
                "[DONE]",
            ),
        )
    )
    events = await collect(make())
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "ab"


@respx.mock
async def test_401_maps_to_auth_error_and_does_not_retry() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(401, text="bad key"))
    with pytest.raises(ModelAuthError) as exc:
        await collect(make(max_retries=3))
    assert route.call_count == 1
    assert exc.value.retryable is False
    # The provider's raw body must not become the user-facing message.
    assert "bad key" not in exc.value.user_message


@respx.mock
async def test_429_maps_to_rate_limit() -> None:
    respx.post(URL).mock(return_value=httpx.Response(429, text="slow down"))
    with pytest.raises(ModelRateLimitedError) as exc:
        await collect(make(max_retries=0))
    assert exc.value.retryable is True


@respx.mock
async def test_5xx_is_retried_then_succeeds() -> None:
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(503, text="upstream down"),
            httpx.Response(200, content=sse('{"choices":[{"delta":{"content":"ok"}}]}', "[DONE]")),
        ]
    )
    events = await collect(make(max_retries=2))
    assert route.call_count == 2
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "ok"


@respx.mock
async def test_retries_are_bounded_and_then_surface() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(ModelError):
        await collect(make(max_retries=2))
    assert route.call_count == 3  # initial + 2 retries


@respx.mock
async def test_timeout_maps_to_model_timeout() -> None:
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(ModelTimeoutError):
        await collect(make(max_retries=0))


@respx.mock
async def test_transport_error_message_is_user_safe() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("failed to connect to 10.0.0.5:5432"))
    with pytest.raises(ModelError) as exc:
        await collect(make(max_retries=0))
    assert "10.0.0.5" not in exc.value.user_message


@respx.mock
async def test_image_to_text_only_model_is_refused_before_the_request() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, content=sse("[DONE]")))
    msg = Message(
        role="user", content="what is this", images=[ImagePart(data_url="data:image/png;base64,x")]
    )
    with pytest.raises(UnsupportedCapabilityError):
        await collect(make(), messages=[msg], model="some/text-only-model")
    # Nothing was sent — the refusal happens before any network call.
    assert route.call_count == 0


@respx.mock
async def test_vision_model_sends_multimodal_content() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(
            200, content=sse('{"choices":[{"delta":{"content":"ok"}}]}', "[DONE]")
        )

    respx.post(URL).mock(side_effect=handler)
    msg = Message(
        role="user", content="describe", images=[ImagePart(data_url="data:image/png;base64,x")]
    )
    await collect(make(), messages=[msg], model="openai/gpt-4o-mini")

    parts = captured["messages"][0]["content"]
    assert {p["type"] for p in parts} == {"text", "image_url"}
    assert captured["stream"] is True
    assert captured["stream_options"] == {"include_usage": True}


@respx.mock
async def test_api_key_is_sent_as_bearer_and_never_in_the_body() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = request.content.decode()
        return httpx.Response(200, content=sse("[DONE]"))

    respx.post(URL).mock(side_effect=handler)
    with pytest.raises(Exception):
        await collect(make(api_key="super-secret"))
    assert captured["auth"] == "Bearer super-secret"
    assert "super-secret" not in captured["body"]


def test_unknown_model_never_claims_vision() -> None:
    info = model_info("some/unreleased-model")
    assert [str(c) for c in info.capabilities] == ["text"]


def test_empty_api_key_is_rejected_at_construction() -> None:
    with pytest.raises(ModelAuthError):
        TokenRouterProvider(api_key="   ", base_url=BASE)


def test_default_model_advertises_vision() -> None:
    """Verified against the live endpoint on 2026-08-01.

    This was wrong in the first cut: kimi-k3-free was listed as text-only on
    assumption, so the UI refused every image attachment for the only model the
    key can use. Posting an image_url part returns 200 and the model answers
    about the image, so the capability is real and the registry must say so.
    """
    info = model_info("moonshotai/kimi-k3-free")
    assert info.supports(Capability.VISION)
    assert info.supports(Capability.TEXT)


def test_vision_model_receives_images_end_to_end() -> None:
    """The provider must not refuse an image for a vision-capable model."""
    provider = make()
    payload = provider._build_payload(
        messages=[
            Message(
                role="user",
                content="describe",
                images=[ImagePart(data_url="data:image/png;base64,x")],
            )
        ],
        model="moonshotai/kimi-k3-free",
        temperature=0.7,
        max_tokens=100,
    )
    parts = payload["messages"][0]["content"]
    assert {p["type"] for p in parts} == {"text", "image_url"}

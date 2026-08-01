from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from kimi import deps
from kimi.config import Settings
from kimi.db import session as db_session
from kimi.errors import ModelError
from kimi.main import create_app
from kimi.providers.base import (
    Capability,
    Message,
    ModelInfo,
    StreamDone,
    StreamEvent,
    TextDelta,
    Timing,
    Usage,
)


class FakeProvider:
    """A provider double with scriptable behaviour.

    ``chunks`` are emitted in order; ``raise_after`` injects a failure once N
    chunks have been delivered, which is how the partial-output-preservation
    behaviour is tested.
    """

    name = "fake"

    def __init__(
        self,
        chunks: Sequence[str] = ("Hello", " world"),
        *,
        raise_after: int | None = None,
        error: Exception | None = None,
        vision: bool = False,
    ) -> None:
        self.chunks = list(chunks)
        self.raise_after = raise_after
        self.error = error or ModelError("The model provider is unavailable.")
        self.vision = vision
        self.calls: list[dict[str, object]] = []

    def get_model(self, model_id: str) -> ModelInfo:
        caps = {Capability.TEXT}
        if self.vision:
            caps.add(Capability.VISION)
        return ModelInfo(model_id, model_id, frozenset(caps), 128_000)

    def list_models(self) -> Sequence[ModelInfo]:
        return (self.get_model("fake/model"),)

    async def stream_chat(
        self,
        *,
        messages: Sequence[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        for i, chunk in enumerate(self.chunks):
            if self.raise_after is not None and i == self.raise_after:
                raise self.error
            yield TextDelta(chunk)
        yield StreamDone(
            finish_reason="stop",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            timing=Timing(first_token_ms=12.0, total_ms=34.0),
        )


@pytest.fixture
def db_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp) / "test.db"


@pytest.fixture
def settings(db_path: Path) -> Settings:
    return Settings(
        environment="local",
        debug=True,
        database_url=f"sqlite+aiosqlite:///{db_path}",
        tokenrouter_api_key="test-key",  # type: ignore[arg-type]
        cors_origins=["http://localhost:3000"],
    )


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
async def client(settings: Settings, provider: FakeProvider) -> AsyncIterator[AsyncClient]:
    await db_session.dispose()
    deps.reset_provider()

    app = create_app(settings)
    app.dependency_overrides[deps.get_settings] = lambda: settings
    app.dependency_overrides[deps.get_provider] = lambda: provider

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Trigger lifespan so tables are created.
        async with app.router.lifespan_context(app):
            yield ac

    await db_session.dispose()
    deps.reset_provider()


@pytest.fixture
async def conversation_id(client: AsyncClient) -> str:
    resp = await client.post("/api/conversations", json={"title": "New chat"})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event, data) pairs."""
    import json

    out: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if event:
            out.append((event, json.loads(data) if data else {}))
    return out

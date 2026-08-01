"""TokenRouter provider (OpenAI-compatible ``/chat/completions``).

Implemented directly on ``httpx`` rather than the ``openai`` SDK so that
timeout, retry, and — critically — *cancellation* semantics are explicit and
testable. When the consumer stops iterating, the ``async with`` blocks unwind
and the socket is closed; no orphaned request keeps streaming in the
background.

Retries are attempted **only before the first token is emitted**. Once any text
has reached the user, retrying would duplicate output, so a mid-stream failure
is surfaced instead of silently retried.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import structlog

from kimi.errors import (
    ModelAuthError,
    ModelBadResponseError,
    ModelError,
    ModelRateLimitedError,
    ModelTimeoutError,
    UnsupportedCapabilityError,
)
from kimi.providers.base import (
    Capability,
    ChatProvider,
    Message,
    ModelInfo,
    StreamDone,
    StreamEvent,
    TextDelta,
    Timing,
    Usage,
)

log = structlog.get_logger(__name__)

_TEXT_ONLY = frozenset({Capability.TEXT})
_TEXT_TOOLS = frozenset({Capability.TEXT, Capability.TOOLS})
_MULTIMODAL = frozenset({Capability.TEXT, Capability.TOOLS, Capability.VISION})

#: Known models. Anything not listed falls back to :data:`_FALLBACK_MODEL`,
#: which claims only TEXT — we never *assume* a capability we cannot confirm.
_KNOWN: dict[str, ModelInfo] = {
    m.id: m
    for m in (
        ModelInfo("moonshotai/kimi-k3-free", "Kimi K3 (free)", _TEXT_TOOLS, 128_000),
        ModelInfo("moonshotai/kimi-k2-thinking", "Kimi K2 Thinking", _TEXT_TOOLS, 256_000),
        ModelInfo("openai/gpt-4o-mini", "GPT-4o mini", _MULTIMODAL, 128_000),
        ModelInfo("openai/gpt-4o", "GPT-4o", _MULTIMODAL, 128_000),
        ModelInfo("anthropic/claude-sonnet-4", "Claude Sonnet 4", _MULTIMODAL, 200_000),
    )
}


def _fallback_model(model_id: str) -> ModelInfo:
    return ModelInfo(model_id, model_id, _TEXT_ONLY, 32_000)


def known_models() -> tuple[ModelInfo, ...]:
    """The static capability registry.

    Exposed without a credential so the model picker can render before the API
    key is configured.
    """
    return tuple(_KNOWN.values())


def model_info(model_id: str) -> ModelInfo:
    """Capabilities for ``model_id``, conservatively text-only when unknown."""
    return _KNOWN.get(model_id) or _fallback_model(model_id)


class TokenRouterProvider(ChatProvider):
    """Streaming chat against an OpenAI-compatible endpoint."""

    name = "tokenrouter"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        request_timeout_s: float = 120.0,
        connect_timeout_s: float = 10.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ModelAuthError(detail="empty api key passed to TokenRouterProvider")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_retries = max(0, max_retries)
        self._timeout = httpx.Timeout(
            request_timeout_s, connect=connect_timeout_s, read=request_timeout_s
        )
        self._client = client
        self._owns_client = client is None

    # -- lifecycle ------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # -- capabilities ---------------------------------------------------

    def get_model(self, model_id: str) -> ModelInfo:
        return _KNOWN.get(model_id) or _fallback_model(model_id)

    def list_models(self) -> Sequence[ModelInfo]:
        return tuple(_KNOWN.values())

    async def fetch_available_models(self) -> list[ModelInfo]:
        """Ask the provider which models this key can actually use.

        The static registry describes capabilities, but it cannot know what a
        given key is entitled to. Listing a model the user cannot call means
        advertising a feature that does not exist, so the picker is built from
        this list and the registry is used only to describe what is on it.
        """
        client = await self._get_client()
        response = await client.get(
            f"{self._base_url}/models",
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
        )
        if response.status_code >= 400:
            self._raise_for_status(response.status_code, response.text)

        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ModelBadResponseError(detail="model list was not a list")

        models: list[ModelInfo] = []
        for row in rows:
            model_id = row.get("id") if isinstance(row, dict) else row
            if isinstance(model_id, str) and model_id:
                models.append(model_info(model_id))
        return models

    # -- request building -----------------------------------------------

    def _build_payload(
        self,
        *,
        messages: Sequence[Message],
        model: str,
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        info = self.get_model(model)
        wire: list[dict[str, Any]] = []

        for msg in messages:
            if not msg.images:
                wire.append({"role": msg.role, "content": msg.content})
                continue

            # Refuse rather than silently dropping the image: the brief
            # requires that images are never omitted without the user knowing.
            if not info.supports(Capability.VISION):
                raise UnsupportedCapabilityError(
                    f"{info.label} cannot read images. Switch to a vision-capable "
                    "model, or remove the image attachment.",
                    context={"model": model, "capability": "vision"},
                )
            parts: list[dict[str, Any]] = []
            if msg.content:
                parts.append({"type": "text", "text": msg.content})
            parts.extend(
                {"type": "image_url", "image_url": {"url": img.data_url, "detail": img.detail}}
                for img in msg.images
            )
            wire.append({"role": msg.role, "content": parts})

        payload: dict[str, Any] = {
            "model": model,
            "messages": wire,
            "temperature": temperature,
            "stream": True,
            # Ask the endpoint to report token usage on the final chunk.
            "stream_options": {"include_usage": True},
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    def _raise_for_status(self, status: int, body: str) -> None:
        snippet = body[:400]
        if status in (401, 403):
            raise ModelAuthError(detail=f"HTTP {status}: {snippet}")
        if status == 429:
            raise ModelRateLimitedError(detail=f"HTTP {status}: {snippet}", retryable=True)
        if status >= 500:
            raise ModelError(
                "The model provider is temporarily unavailable.",
                detail=f"HTTP {status}: {snippet}",
                retryable=True,
            )
        if status >= 400:
            raise ModelError(
                "The model provider rejected this request.",
                detail=f"HTTP {status}: {snippet}",
            )

    # -- streaming ------------------------------------------------------

    async def stream_chat(
        self,
        *,
        messages: Sequence[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        payload = self._build_payload(
            messages=messages, model=model, temperature=temperature, max_tokens=max_tokens
        )
        client = await self._get_client()
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        started = time.perf_counter()
        first_token_ms: float | None = None
        usage = Usage()
        finish_reason: str | None = None
        emitted = False
        attempt = 0

        while True:
            try:
                async with client.stream(
                    "POST", url, json=payload, headers=headers, timeout=self._timeout
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", "replace")
                        self._raise_for_status(response.status_code, body)

                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            log.warning("provider.bad_chunk", provider=self.name)
                            continue

                        if (u := chunk.get("usage")) is not None:
                            usage = Usage(
                                prompt_tokens=u.get("prompt_tokens"),
                                completion_tokens=u.get("completion_tokens"),
                                total_tokens=u.get("total_tokens"),
                            )

                        for choice in chunk.get("choices") or ():
                            if (fr := choice.get("finish_reason")) is not None:
                                finish_reason = fr
                            text = (choice.get("delta") or {}).get("content")
                            if not text:
                                continue
                            if first_token_ms is None:
                                first_token_ms = (time.perf_counter() - started) * 1000
                            emitted = True
                            yield TextDelta(text)
                break

            except (httpx.TimeoutException, httpx.TransportError, ModelError) as exc:
                retryable = getattr(exc, "retryable", True)
                # Never retry once output has reached the user: it would duplicate text.
                if emitted or not retryable or attempt >= self._max_retries:
                    if isinstance(exc, httpx.TimeoutException):
                        raise ModelTimeoutError(detail=str(exc)) from exc
                    if isinstance(exc, httpx.TransportError):
                        raise ModelError(
                            "Could not reach the model provider. Check your network.",
                            detail=str(exc),
                        ) from exc
                    raise
                attempt += 1
                backoff = 0.5 * (2 ** (attempt - 1))
                log.warning(
                    "provider.retry", provider=self.name, attempt=attempt, backoff_s=backoff
                )
                await asyncio.sleep(backoff)

        total_ms = (time.perf_counter() - started) * 1000
        if not emitted and finish_reason is None:
            raise ModelBadResponseError(detail="stream closed without any content")

        yield StreamDone(
            finish_reason=finish_reason,
            usage=usage,
            timing=Timing(first_token_ms=first_token_ms, total_ms=total_ms),
        )

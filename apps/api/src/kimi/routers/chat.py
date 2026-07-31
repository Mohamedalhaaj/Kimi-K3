"""Streaming chat over Server-Sent Events.

Two failure behaviours the prototype got wrong are fixed here and covered by
tests:

1. **Partial output is never discarded.** The prototype's ``except`` block
   *overwrote* the accumulated text with an error string, erasing every token the
   user had already watched arrive (docs/AUDIT.md §5, ``app.py:719-725``). Here
   the assistant row is persisted in a ``finally`` with whatever text arrived,
   and the error is recorded in a separate column.

2. **No orphaned user turns.** The prototype committed the user message before
   the API call and the assistant message only after post-processing, so any
   mid-stream failure left two consecutive user turns. Here both rows are
   written, and a cancelled or failed stream still closes the turn.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from kimi.config import Settings
from kimi.db.base import Conversation, utcnow
from kimi.db.base import Message as DbMessage
from kimi.db.session import get_sessionmaker
from kimi.deps import ProviderDep, SettingsDep
from kimi.errors import ErrorCode, KimiError, NotFoundError
from kimi.providers.base import Capability, ChatProvider, StreamDone, TextDelta
from kimi.schemas.api import ChatRequest
from kimi.services.context import (
    DEFAULT_SYSTEM_PROMPT,
    PRESETS,
    build_context,
    estimate_tokens,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


def _sse(event: str, data: dict[str, Any]) -> str:
    """Encode one SSE frame.

    ``json.dumps`` guarantees the payload contains no raw newline, which would
    otherwise terminate the frame early and corrupt the stream.
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def stream_chat(
    payload: ChatRequest,
    request: Request,
    settings: SettingsDep,
    provider: ProviderDep,
) -> StreamingResponse:
    return StreamingResponse(
        _generate(payload, request, settings, provider),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _generate(
    payload: ChatRequest,
    request: Request,
    settings: Settings,
    provider: ChatProvider,
) -> AsyncIterator[str]:
    sessionmaker = get_sessionmaker(settings)
    started = time.perf_counter()

    # ---- write the user turn ------------------------------------------
    async with sessionmaker() as session:
        convo = await session.get(Conversation, payload.conversation_id)
        if convo is None:
            yield _sse(
                "error",
                {
                    "code": str(ErrorCode.NOT_FOUND),
                    "message": NotFoundError().user_message,
                    "retryable": False,
                },
            )
            return

        mode = payload.mode or convo.mode or "balanced"
        if mode not in PRESETS:
            mode = "balanced"
        model_id = payload.model_id or convo.model_id or settings.default_model
        preset = PRESETS[mode]  # type: ignore[index]
        info = provider.get_model(model_id)

        next_seq = (
            await session.execute(
                select(func.coalesce(func.max(DbMessage.seq), -1) + 1).where(
                    DbMessage.conversation_id == convo.id
                )
            )
        ).scalar_one()

        user_row = DbMessage(
            conversation_id=convo.id, seq=next_seq, role="user", content=payload.content
        )
        session.add(user_row)

        # First real message names the conversation — deterministic, no model call.
        if next_seq == 0 and convo.title in ("", "New chat"):
            convo.title = payload.content.strip().splitlines()[0][:80] or "New chat"
        convo.updated_at = utcnow()
        convo.model_id = model_id
        convo.mode = mode
        await session.commit()

        history = (
            (
                await session.execute(
                    select(DbMessage)
                    .where(DbMessage.conversation_id == convo.id)
                    .order_by(DbMessage.seq)
                )
            )
            .scalars()
            .all()
        )
        assistant_seq = next_seq + 1
        conversation_title = convo.title

    built = build_context(
        history=history,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        context_window=info.context_window,
        mode=mode,  # type: ignore[arg-type]
        supports_vision=info.supports(Capability.VISION),
        pending_images=[i.data_url for i in payload.images],
    )

    yield _sse(
        "start",
        {
            "conversation_id": payload.conversation_id,
            "title": conversation_title,
            "user_message_id": user_row.id,
            "model_id": model_id,
            "mode": mode,
            "context": {
                "included_messages": built.report.included_messages,
                "dropped_messages": built.report.dropped_messages,
                "estimated_prompt_tokens": built.report.estimated_prompt_tokens,
                "budget_tokens": built.report.budget_tokens,
                "dropped_images": built.report.dropped_images,
            },
        },
    )

    # Tell the user immediately when their images could not be sent, rather
    # than silently dropping them.
    if built.report.dropped_images:
        yield _sse(
            "warning",
            {
                "code": str(ErrorCode.UNSUPPORTED_CAPABILITY),
                "message": (
                    f"{info.label} cannot read images — "
                    f"{built.report.dropped_images} attachment(s) were not sent."
                ),
            },
        )

    # ---- stream the assistant turn ------------------------------------
    chunks: list[str] = []
    error_payload: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    timing: dict[str, Any] | None = None
    finish_reason: str | None = None
    cancelled = False

    try:
        async for event in provider.stream_chat(
            messages=built.messages,
            model=model_id,
            temperature=preset.temperature,
            max_tokens=preset.max_output_tokens,
        ):
            if await request.is_disconnected():
                cancelled = True
                break
            if isinstance(event, TextDelta):
                chunks.append(event.text)
                yield _sse("delta", {"text": event.text})
            elif isinstance(event, StreamDone):
                finish_reason = event.finish_reason
                usage = {
                    "prompt_tokens": event.usage.prompt_tokens,
                    "completion_tokens": event.usage.completion_tokens,
                    "total_tokens": event.usage.total_tokens,
                }
                timing = {
                    "first_token_ms": event.timing.first_token_ms,
                    "total_ms": event.timing.total_ms,
                }

    except asyncio.CancelledError:
        cancelled = True
        raise
    except KimiError as exc:
        error_payload = exc.to_payload(include_detail=settings.debug)
        log.warning("chat.provider_error", code=str(exc.code), detail=exc.detail)
    except Exception as exc:
        error_payload = {
            "code": str(ErrorCode.INTERNAL),
            "message": "The response could not be completed.",
            "retryable": True,
        }
        log.error("chat.unhandled", exc_type=type(exc).__name__)
    finally:
        text = "".join(chunks)
        if cancelled and not error_payload:
            error_payload = {
                "code": str(ErrorCode.CANCELLED),
                "message": "Generation stopped.",
                "retryable": False,
            }
        # Persist whatever arrived. Partial text is real output the user saw and
        # is never replaced by the error string.
        if timing is None:
            timing = {
                "first_token_ms": None,
                "total_ms": (time.perf_counter() - started) * 1000,
            }
        try:
            async with sessionmaker() as session:
                session.add(
                    DbMessage(
                        conversation_id=payload.conversation_id,
                        seq=assistant_seq,
                        role="assistant",
                        content=text,
                        model_id=model_id,
                        usage=usage,
                        timing=timing,
                        error=error_payload,
                    )
                )
                convo = await session.get(Conversation, payload.conversation_id)
                if convo is not None:
                    convo.updated_at = utcnow()
                await session.commit()
        except Exception as exc:
            log.error("chat.persist_failed", exc_type=type(exc).__name__)

    if error_payload:
        yield _sse("error", error_payload)

    yield _sse(
        "done",
        {
            "finish_reason": finish_reason,
            "usage": usage,
            "timing": timing,
            "estimated_output_tokens": estimate_tokens("".join(chunks)),
            "assistant_seq": assistant_seq,
        },
    )

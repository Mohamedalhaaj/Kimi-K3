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
from kimi.db.base import Attachment, Conversation, utcnow
from kimi.db.base import Message as DbMessage
from kimi.db.session import get_sessionmaker
from kimi.deps import ProviderDep, SettingsDep
from kimi.errors import ErrorCode, KimiError, NotFoundError
from kimi.files.service import to_prompt_block as documents_to_prompt_block
from kimi.providers.base import Capability, ChatProvider, StreamDone, TextDelta
from kimi.schemas.api import ChatRequest
from kimi.services.context import (
    DEFAULT_SYSTEM_PROMPT,
    PRESETS,
    build_context,
    estimate_tokens,
)
from kimi.services.toolrouter import ResearchMode, route
from kimi.tools.base import ToolInvocation, ToolStatus
from kimi.tools.registry import ToolEngine

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_tool_engine = ToolEngine()


def _format_deterministic(tool_id: str, invocation: ToolInvocation) -> str:
    """Render a deterministic tool's result as the assistant's answer.

    Written here rather than by the model: that is the whole point of a
    deterministic tool.
    """
    if invocation.error:
        return ""
    result = invocation.result or {}
    if tool_id == "calculator":
        return f"{result.get('expression', '')} = **{result.get('result', '')}**"
    return str(result)


def _documents_block(rows: list[Attachment]) -> str:
    """Render stored attachments as the fenced untrusted-documents block.

    Rebuilds the parsed shape from the database so a conversation reloaded after
    a restart produces exactly the same context as when it was first sent.
    """
    if not rows:
        return ""

    from kimi.files.models import (
        DocumentKind,
        ParsedDocument,
        ParseStatus,
        RefKind,
        Segment,
        SegmentRef,
    )

    documents: list[ParsedDocument] = []
    for row in rows:
        doc = ParsedDocument(
            id=row.id,
            filename=row.filename,
            kind=DocumentKind(row.kind),
            status=ParseStatus(row.status),
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            summary=row.summary,
            metadata=row.doc_metadata or {},
            warnings=list(row.warnings or []),
        )
        for raw in row.segments or []:
            ref = raw.get("ref", {})
            doc.segments.append(
                Segment(
                    ref=SegmentRef(
                        RefKind(ref.get("kind", "whole")),
                        int(ref.get("number", 0) or 0),
                        str(ref.get("name", "")),
                    ),
                    text=str(raw.get("text", "")),
                    truncated=bool(raw.get("truncated")),
                )
            )
        documents.append(doc)
    return documents_to_prompt_block(documents)


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

        attachments: list[Attachment] = []
        if payload.document_ids:
            rows = (
                (
                    await session.execute(
                        select(Attachment).where(
                            Attachment.id.in_(payload.document_ids),
                            # Scoped to this conversation: a file id from another
                            # conversation must not be readable by guessing it.
                            Attachment.conversation_id == convo.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            attachments = list(rows)

    yield _sse(
        "start",
        {
            "conversation_id": payload.conversation_id,
            "title": conversation_title,
            "user_message_id": user_row.id,
            "model_id": model_id,
            "mode": mode,
            "attachments": [
                {"id": a.id, "filename": a.filename, "status": a.status, "kind": a.kind}
                for a in attachments
            ],
        },
    )

    # ---- tools ---------------------------------------------------------
    # The router is deterministic: choosing a tool never costs a token.
    plan = route(payload.content, research=ResearchMode(payload.research))
    tool_payload: dict[str, Any] | None = None
    tool_context_block = ""
    citations: list[dict[str, Any]] = []

    if plan.tool_id:
        yield _sse(
            "tool",
            {
                "status": str(ToolStatus.RUNNING),
                "tool_id": plan.tool_id,
                "arguments": plan.arguments,
                "reason": plan.reason,
            },
        )
        invocation = await _tool_engine.execute(
            plan.tool_id, plan.arguments, conversation_id=payload.conversation_id
        )
        tool_payload = invocation.to_payload()
        yield _sse("tool", tool_payload)

        result = invocation.result or {}
        if invocation.status.is_terminal and invocation.error is None:
            tool_context_block = str(result.get("prompt_block") or "")
            raw_sources = result.get("sources")
            if isinstance(raw_sources, list):
                citations = [s for s in raw_sources if isinstance(s, dict)]
                if citations:
                    yield _sse("sources", {"sources": citations})

        # A deterministic tool's output IS the answer. Persist it and stop —
        # no provider call, no tokens. This is the behaviour the prototype
        # achieved by monkeypatching the OpenAI SDK's Completions class.
        if plan.deterministic:
            text = _format_deterministic(plan.tool_id, invocation)
            async with sessionmaker() as session:
                session.add(
                    DbMessage(
                        conversation_id=payload.conversation_id,
                        seq=assistant_seq,
                        role="assistant",
                        content=text,
                        model_id=None,
                        usage=None,
                        timing={
                            "first_token_ms": None,
                            "total_ms": (time.perf_counter() - started) * 1000,
                            "tool_ms": invocation.duration_ms,
                        },
                        error=invocation.error,
                        tool=tool_payload,
                    )
                )
                convo = await session.get(Conversation, payload.conversation_id)
                if convo is not None:
                    convo.updated_at = utcnow()
                await session.commit()

            if text:
                yield _sse("delta", {"text": text})
            if invocation.error:
                yield _sse("error", invocation.error)
            yield _sse(
                "done",
                {
                    "finish_reason": "tool",
                    "usage": None,
                    "timing": {
                        "first_token_ms": None,
                        "total_ms": (time.perf_counter() - started) * 1000,
                        "tool_ms": round(invocation.duration_ms, 1),
                    },
                    "model_called": False,
                    "assistant_seq": assistant_seq,
                },
            )
            return

    # Tool output is appended as a separate, clearly-fenced turn rather than
    # concatenated into the user's own message, so the model can tell the
    # user's words apart from retrieved data.
    system_prompt = DEFAULT_SYSTEM_PROMPT
    document_block = _documents_block(attachments)
    if document_block:
        system_prompt = f"{system_prompt}\n\n{document_block}"
    if tool_context_block:
        system_prompt = f"{system_prompt}\n\n{tool_context_block}"

    # Images attached as documents travel with the turn like inline images do.
    pending_images = [i.data_url for i in payload.images]
    pending_images += [a.image_data_url for a in attachments if a.image_data_url]

    built = build_context(
        history=history,
        system_prompt=system_prompt,
        context_window=info.context_window,
        mode=mode,  # type: ignore[arg-type]
        supports_vision=info.supports(Capability.VISION),
        pending_images=pending_images,
    )

    yield _sse(
        "context",
        {
            "included_messages": built.report.included_messages,
            "dropped_messages": built.report.dropped_messages,
            "estimated_prompt_tokens": built.report.estimated_prompt_tokens,
            "budget_tokens": built.report.budget_tokens,
            "dropped_images": built.report.dropped_images,
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
                        tool=tool_payload,
                        citations=citations or None,
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
            "model_called": True,
            "tool_ms": (tool_payload or {}).get("duration_ms"),
            "citations": len(citations),
        },
    )

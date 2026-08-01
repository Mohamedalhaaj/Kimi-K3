"""Artifact export endpoints.

Every artifact is generated on demand from stored data rather than cached, so a
download always reflects the conversation as it stands. Filenames are derived
from the conversation title, sanitised, and timestamped, so repeated exports do
not silently overwrite one another.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from kimi.db.base import Conversation, Message
from kimi.deps import SessionDep
from kimi.errors import InvalidRequestError, NotFoundError
from kimi.exports.writers import (
    DOCX_MIME,
    XLSX_MIME,
    answer_to_docx,
    conversation_to_json,
    conversation_to_markdown,
    safe_stem,
    sources_to_csv,
    sources_to_xlsx,
    timestamped,
)

router = APIRouter(prefix="/exports", tags=["exports"])

Format = Literal["md", "json", "docx", "csv", "xlsx"]


def _attachment(data: bytes | str, filename: str, media_type: str) -> Response:
    body = data.encode("utf-8") if isinstance(data, str) else data
    # RFC 5987 so Arabic filenames survive the round trip.
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(len(body)),
        },
    )


async def _load(session: SessionDep, conversation_id: str) -> Conversation:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.messages))
    )
    convo = result.scalar_one_or_none()
    if convo is None:
        raise NotFoundError("That conversation no longer exists.")
    return convo


def _messages_payload(convo: Conversation) -> list[dict[str, Any]]:
    return [
        {
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at.isoformat(),
            "model_id": m.model_id,
            "usage": m.usage,
            "timing": m.timing,
            "citations": m.citations,
        }
        for m in convo.messages
    ]


def _collect_sources(convo: Conversation) -> list[dict[str, Any]]:
    """De-duplicate citations across the conversation, keeping first-seen order."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for message in convo.messages:
        for citation in message.citations or []:
            url = str(citation.get("url") or "")
            if url and url not in seen:
                seen.add(url)
                out.append(dict(citation) | {"index": len(out) + 1})
    return out


@router.get("/conversation/{conversation_id}")
async def export_conversation(
    conversation_id: str, session: SessionDep, format: Format = "md"
) -> Response:
    convo = await _load(session, conversation_id)
    stem = safe_stem(convo.title, "conversation")
    messages = _messages_payload(convo)

    if format == "md":
        return _attachment(
            conversation_to_markdown(title=convo.title, messages=messages),
            timestamped(stem, "md"),
            "text/markdown; charset=utf-8",
        )
    if format == "json":
        return _attachment(
            conversation_to_json(title=convo.title, messages=messages),
            timestamped(stem, "json"),
            "application/json; charset=utf-8",
        )
    if format == "docx":
        body = "\n\n".join(
            f"## {'You' if m['role'] == 'user' else 'Kimi'}\n\n{m['content']}" for m in messages
        )
        return _attachment(
            answer_to_docx(
                title=convo.title,
                body_markdown=body,
                sources=_collect_sources(convo),
                subtitle="Exported from Kimi Workspace",
            ),
            timestamped(stem, "docx"),
            DOCX_MIME,
        )
    raise InvalidRequestError(f"{format!r} is not a conversation export format.")


@router.get("/message/{message_id}")
async def export_message(message_id: str, session: SessionDep, format: Format = "docx") -> Response:
    message = await session.get(Message, message_id)
    if message is None:
        raise NotFoundError("That message no longer exists.")
    convo = await session.get(Conversation, message.conversation_id)
    title = convo.title if convo else "Answer"
    stem = safe_stem(title, "answer")
    sources = [dict(c) | {"index": i + 1} for i, c in enumerate(message.citations or [])]

    if format == "docx":
        return _attachment(
            answer_to_docx(
                title=title,
                body_markdown=message.content,
                sources=sources,
                subtitle=f"Answer exported {message.created_at:%Y-%m-%d %H:%M} UTC",
            ),
            timestamped(stem, "docx"),
            DOCX_MIME,
        )
    if format == "md":
        return _attachment(message.content, timestamped(stem, "md"), "text/markdown; charset=utf-8")
    if format == "csv":
        if not sources:
            raise InvalidRequestError("That answer has no sources to export.")
        return _attachment(
            sources_to_csv(sources), timestamped(stem, "csv"), "text/csv; charset=utf-8"
        )
    if format == "xlsx":
        if not sources:
            raise InvalidRequestError("That answer has no sources to export.")
        return _attachment(sources_to_xlsx(sources), timestamped(stem, "xlsx"), XLSX_MIME)
    raise InvalidRequestError(f"{format!r} is not a message export format.")

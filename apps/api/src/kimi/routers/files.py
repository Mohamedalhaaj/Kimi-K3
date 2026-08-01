"""Upload and parse documents.

The upload is parsed immediately and only the *result* is stored. The bytes are
never written to disk, so there is no temp directory to clean up and no path for
a crafted filename to escape into.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, UploadFile, status
from sqlalchemy import select

from kimi.db.base import Attachment, Conversation
from kimi.deps import SessionDep
from kimi.errors import InvalidRequestError, NotFoundError
from kimi.files.detect import MAX_UPLOAD_BYTES
from kimi.files.service import parse_upload

router = APIRouter(prefix="/files", tags=["files"])

MAX_FILES_PER_REQUEST = 6


def _to_payload(row: Attachment) -> dict[str, Any]:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "filename": row.filename,
        "kind": row.kind,
        "status": row.status,
        "mime_type": row.mime_type,
        "size_bytes": row.size_bytes,
        "summary": row.summary,
        "metadata": row.doc_metadata or {},
        "warnings": row.warnings or [],
        "segment_count": len(row.segments or []),
        "has_image": bool(row.image_data_url),
        "created_at": row.created_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_files(
    session: SessionDep,
    conversation_id: str,
    files: list[UploadFile] = File(...),  # noqa: B008 - FastAPI's declaration style
) -> dict[str, Any]:
    """Parse uploads and attach them to a conversation."""
    convo = await session.get(Conversation, conversation_id)
    if convo is None:
        raise NotFoundError("That conversation no longer exists.")
    if not files:
        raise InvalidRequestError("No files were uploaded.")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise InvalidRequestError(f"Attach at most {MAX_FILES_PER_REQUEST} files at once.")

    out: list[dict[str, Any]] = []
    for upload in files:
        # Read with a hard ceiling rather than trusting any declared length.
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        await upload.close()

        parsed = await parse_upload(upload.filename or "untitled", data)
        row = Attachment(
            conversation_id=conversation_id,
            filename=parsed.filename,
            kind=str(parsed.kind),
            status=str(parsed.status),
            mime_type=parsed.mime_type,
            size_bytes=parsed.size_bytes,
            summary=parsed.summary,
            segments=[s.to_payload() for s in parsed.segments],
            doc_metadata=parsed.metadata,
            warnings=parsed.warnings,
            image_data_url=parsed.image_data_url or None,
        )
        session.add(row)
        await session.flush()
        out.append(_to_payload(row))

    await session.commit()
    return {"files": out}


@router.get("")
async def list_files(session: SessionDep, conversation_id: str) -> dict[str, Any]:
    rows = (
        (
            await session.execute(
                select(Attachment)
                .where(Attachment.conversation_id == conversation_id)
                .order_by(Attachment.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {"files": [_to_payload(r) for r in rows]}


@router.get("/{file_id}")
async def get_file(session: SessionDep, file_id: str) -> dict[str, Any]:
    """Full record including the citable segments."""
    row = await session.get(Attachment, file_id)
    if row is None:
        raise NotFoundError("That file is no longer available.")
    payload = _to_payload(row)
    payload["segments"] = row.segments or []
    return payload


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(session: SessionDep, file_id: str) -> None:
    row = await session.get(Attachment, file_id)
    if row is None:
        raise NotFoundError("That file is no longer available.")
    await session.delete(row)
    await session.commit()

"""Conversation CRUD.

Deliberately boring: these are the deterministic operations the brief requires
to return immediately without ever consulting a model. Renaming a conversation
must not cost a token.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from kimi.db.base import Conversation, Message, utcnow
from kimi.deps import SessionDep
from kimi.errors import NotFoundError
from kimi.schemas.api import (
    ConversationCreate,
    ConversationDetail,
    ConversationList,
    ConversationOut,
    ConversationUpdate,
    Page,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _get_or_404(session: SessionDep, conversation_id: str) -> Conversation:
    convo = await session.get(Conversation, conversation_id)
    if convo is None:
        raise NotFoundError("That conversation no longer exists.")
    return convo


@router.get("", response_model=ConversationList)
async def list_conversations(
    session: SessionDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=200, description="Title search"),
) -> ConversationList:
    stmt = select(Conversation)
    count_stmt = select(func.count()).select_from(Conversation)
    if q:
        # Bound parameter — never string-interpolated into SQL.
        pattern = f"%{q}%"
        stmt = stmt.where(Conversation.title.ilike(pattern))
        count_stmt = count_stmt.where(Conversation.title.ilike(pattern))

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        (
            await session.execute(
                stmt.order_by(Conversation.pinned.desc(), Conversation.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return ConversationList(
        items=[ConversationOut.model_validate(r) for r in rows],
        page=Page(total=total, limit=limit, offset=offset),
    )


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(payload: ConversationCreate, session: SessionDep) -> ConversationOut:
    convo = Conversation(
        title=payload.title,
        project_id=payload.project_id,
        model_id=payload.model_id,
        mode=payload.mode,
    )
    session.add(convo)
    await session.commit()
    return ConversationOut.model_validate(convo)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str, session: SessionDep) -> ConversationDetail:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.messages))
    )
    convo = result.scalar_one_or_none()
    if convo is None:
        raise NotFoundError("That conversation no longer exists.")
    return ConversationDetail.model_validate(convo)


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: str, payload: ConversationUpdate, session: SessionDep
) -> ConversationOut:
    convo = await _get_or_404(session, conversation_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(convo, field, value)
    convo.updated_at = utcnow()
    await session.commit()
    return ConversationOut.model_validate(convo)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str, session: SessionDep) -> None:
    convo = await _get_or_404(session, conversation_id)
    await session.delete(convo)
    await session.commit()


@router.delete("/{conversation_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
async def clear_messages(conversation_id: str, session: SessionDep) -> None:
    """Clear context without deleting the conversation."""
    await _get_or_404(session, conversation_id)
    await session.execute(delete(Message).where(Message.conversation_id == conversation_id))
    await session.commit()

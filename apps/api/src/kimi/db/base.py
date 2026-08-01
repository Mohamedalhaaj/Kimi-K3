"""SQLAlchemy models.

Design notes:

* String UUID primary keys, so a conversation can be exported and re-imported
  across databases without id collisions.
* Every timestamp is timezone-aware UTC. ``datetime.utcnow`` is deliberately not
  used anywhere — it is deprecated and returns a naive value, which was the
  source of the prototype's timezone bugs.
* ``Message.seq`` gives a stable total order within a conversation that does not
  depend on timestamp resolution.
* Deleting a conversation deletes its messages (and a project deletes its
  conversations) via ``ON DELETE CASCADE`` *and* the ORM cascade, so the data
  retention promise holds whether the delete goes through the ORM or raw SQL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    #: Extra system instructions applied to every conversation in the project.
    instructions: Mapped[str | None] = mapped_column(Text, default=None)
    default_model: Mapped[str | None] = mapped_column(String(200), default=None)

    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), default=None, index=True
    )
    title: Mapped[str] = mapped_column(String(300), default="New chat")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    model_id: Mapped[str | None] = mapped_column(String(200), default=None)
    mode: Mapped[str] = mapped_column(String(32), default="balanced")

    project: Mapped[Project | None] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.seq",
    )

    __table_args__ = (Index("ix_conversations_pinned_updated", "pinned", "updated_at"),)


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    #: Monotonic within a conversation; unique together with conversation_id.
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")

    model_id: Mapped[str | None] = mapped_column(String(200), default=None)
    #: Provider-reported token usage; None when the provider did not report it.
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    #: Measured latencies (first_token_ms, total_ms). Never estimated.
    timing: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    #: Structured error payload when the turn failed; keeps partial content.
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    #: The tool invocation record for this turn, if a tool ran.
    tool: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    #: Numbered sources backing the answer, in citation order.
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=None)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (Index("ix_messages_conversation_seq", "conversation_id", "seq", unique=True),)

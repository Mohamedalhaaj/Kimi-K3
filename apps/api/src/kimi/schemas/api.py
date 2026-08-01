"""Wire schemas. These are the contract the frontend types are generated from."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ChatMode = Literal["fast", "balanced", "deep"]
ResearchMode = Literal["off", "auto", "always"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- conversations -------------------------------------------------------


class ConversationCreate(BaseModel):
    title: Annotated[str, Field(max_length=300)] = "New chat"
    project_id: str | None = None
    model_id: str | None = None
    mode: ChatMode = "balanced"


class ConversationUpdate(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    pinned: bool | None = None
    model_id: str | None = None
    mode: ChatMode | None = None
    project_id: str | None = None


class ConversationOut(ORMModel):
    id: str
    title: str
    pinned: bool
    project_id: str | None
    model_id: str | None
    mode: str
    created_at: datetime
    updated_at: datetime


class MessageOut(ORMModel):
    id: str
    conversation_id: str
    seq: int
    role: str
    content: str
    model_id: str | None
    usage: dict[str, Any] | None
    timing: dict[str, Any] | None
    error: dict[str, Any] | None
    tool: dict[str, Any] | None = None
    citations: list[dict[str, Any]] | None = None
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class Page(BaseModel):
    """Cursor-free pagination — total plus a bounded window."""

    total: int
    limit: int
    offset: int


class ConversationList(BaseModel):
    items: list[ConversationOut]
    page: Page


# ---- chat ----------------------------------------------------------------


class ImageIn(BaseModel):
    #: ``data:image/png;base64,...``
    data_url: Annotated[str, Field(max_length=12_000_000)]


class ChatRequest(BaseModel):
    conversation_id: str
    content: Annotated[str, Field(min_length=1, max_length=100_000)]
    model_id: str | None = None
    mode: ChatMode | None = None
    images: Annotated[list[ImageIn], Field(max_length=8)] = []
    research: ResearchMode = "auto"


class ErrorOut(BaseModel):
    code: str
    message: str
    retryable: bool = False
    detail: str | None = None
    context: dict[str, Any] | None = None

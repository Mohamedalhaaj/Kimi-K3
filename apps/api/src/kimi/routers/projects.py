"""Project workspaces.

A project groups conversations and carries instructions and a default model
that its conversations inherit. Deleting a project deletes its conversations,
their messages and their attachments — the data-retention promise the brief
asks for, enforced by ON DELETE CASCADE rather than by application code.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from kimi.db.base import Conversation, Project, utcnow
from kimi.deps import SessionDep
from kimi.errors import NotFoundError

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    instructions: str | None = Field(default=None, max_length=8000)
    default_model: str | None = None


class ProjectUpdate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    instructions: str | None = Field(default=None, max_length=8000)
    default_model: str | None = None


def _payload(project: Project, conversations: int = 0) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "instructions": project.instructions,
        "default_model": project.default_model,
        "conversation_count": conversations,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


@router.get("")
async def list_projects(session: SessionDep) -> dict[str, Any]:
    rows_counts = (
        await session.execute(
            select(Conversation.project_id, func.count())
            .where(Conversation.project_id.is_not(None))
            .group_by(Conversation.project_id)
        )
    ).all()
    counts: dict[str, int] = {str(pid): int(n) for pid, n in rows_counts}
    rows = (
        (await session.execute(select(Project).order_by(Project.updated_at.desc()))).scalars().all()
    )
    return {"projects": [_payload(p, counts.get(p.id, 0)) for p in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate, session: SessionDep) -> dict[str, Any]:
    project = Project(
        name=payload.name,
        instructions=payload.instructions,
        default_model=payload.default_model,
    )
    session.add(project)
    await session.commit()
    return _payload(project)


@router.get("/{project_id}")
async def get_project(project_id: str, session: SessionDep) -> dict[str, Any]:
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError("That project no longer exists.")
    conversations = (
        (
            await session.execute(
                select(Conversation)
                .where(Conversation.project_id == project_id)
                .order_by(Conversation.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return _payload(project, len(conversations)) | {
        "conversations": [
            {
                "id": c.id,
                "title": c.title,
                "pinned": c.pinned,
                "updated_at": c.updated_at.isoformat(),
            }
            for c in conversations
        ]
    }


@router.patch("/{project_id}")
async def update_project(
    project_id: str, payload: ProjectUpdate, session: SessionDep
) -> dict[str, Any]:
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError("That project no longer exists.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    project.updated_at = utcnow()
    await session.commit()
    return _payload(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, session: SessionDep) -> None:
    """Deletes the project and everything inside it."""
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError("That project no longer exists.")
    await session.delete(project)
    await session.commit()

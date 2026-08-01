"""Baseline schema: projects, conversations, messages.

This is the schema as it stood before the tool registry landed. An existing
local database created by ``create_all`` can be brought under migration control
with ``alembic stamp 0001`` and then upgraded, which preserves its rows.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("default_model", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_projects_created_at", "projects", ["created_at"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=True),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversations_project_id", "conversations", ["project_id"])
    op.create_index("ix_conversations_pinned", "conversations", ["pinned"])
    op.create_index("ix_conversations_created_at", "conversations", ["created_at"])
    op.create_index("ix_conversations_pinned_updated", "conversations", ["pinned", "updated_at"])

    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("timing", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])
    op.create_index(
        "ix_messages_conversation_seq", "messages", ["conversation_id", "seq"], unique=True
    )


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("projects")

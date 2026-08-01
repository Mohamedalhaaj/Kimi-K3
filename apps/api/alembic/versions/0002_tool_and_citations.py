"""Add message.tool and message.citations for the tool registry.

Adding these to the model without a migration is exactly what broke the running
local app: ``create_all`` creates missing *tables*, never missing *columns*, so
the API started fine and then failed at query time with
``no such column: messages.tool``.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table so SQLite gets a table rewrite and PostgreSQL a plain
    # ALTER. Existing rows keep their data; the new columns default to NULL.
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("tool", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("citations", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("citations")
        batch.drop_column("tool")

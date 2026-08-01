"""Full-text search over messages and attachment text.

SQLite FTS5 gives real ranked search without adding a dependency. The virtual
table is kept in sync by triggers rather than application code, so a write that
bypasses the ORM cannot leave the index stale.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    if not _is_sqlite():
        # PostgreSQL deployments use tsvector; not built here because the
        # supported target today is SQLite and an untested branch is worse
        # than an absent one.
        return

    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
            kind UNINDEXED,
            ref_id UNINDEXED,
            conversation_id UNINDEXED,
            title,
            body,
            tokenize = "unicode61 remove_diacritics 2"
        )
        """
    )

    # Messages
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO search_index(kind, ref_id, conversation_id, title, body)
            VALUES ('message', new.id, new.conversation_id, new.role, new.content);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
            DELETE FROM search_index WHERE kind='message' AND ref_id = old.id;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
            DELETE FROM search_index WHERE kind='message' AND ref_id = old.id;
            INSERT INTO search_index(kind, ref_id, conversation_id, title, body)
            VALUES ('message', new.id, new.conversation_id, new.role, new.content);
        END
        """
    )

    # Attachments: the summary plus the parsed text, so a document is findable
    # by its contents and not only by its filename.
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS attachments_ai AFTER INSERT ON attachments BEGIN
            INSERT INTO search_index(kind, ref_id, conversation_id, title, body)
            VALUES ('file', new.id, new.conversation_id, new.filename,
                    new.summary || ' ' || COALESCE(new.segments, ''));
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS attachments_ad AFTER DELETE ON attachments BEGIN
            DELETE FROM search_index WHERE kind='file' AND ref_id = old.id;
        END
        """
    )

    # Backfill anything already stored.
    op.execute(
        """
        INSERT INTO search_index(kind, ref_id, conversation_id, title, body)
        SELECT 'message', id, conversation_id, role, content FROM messages
        """
    )
    op.execute(
        """
        INSERT INTO search_index(kind, ref_id, conversation_id, title, body)
        SELECT 'file', id, conversation_id, filename,
               summary || ' ' || COALESCE(segments, '') FROM attachments
        """
    )


def downgrade() -> None:
    if not _is_sqlite():
        return
    for trigger in (
        "messages_ai",
        "messages_ad",
        "messages_au",
        "attachments_ai",
        "attachments_ad",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS search_index")

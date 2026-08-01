"""Async engine and session management.

SQLite needs two pragmas applied to *every* connection:

``foreign_keys=ON``
    SQLite ignores foreign keys unless asked. Without this the ``ON DELETE
    CASCADE`` on messages and conversations silently does nothing, and deleting
    a project would orphan its data rather than remove it.

``journal_mode=WAL``
    Lets the streaming read path continue while a write commits, which matters
    because a chat turn writes while the conversation list is being read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from kimi.config import Settings
from kimi.db.base import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def create_engine(settings: Settings) -> AsyncEngine:
    url = settings.database_url
    if _is_sqlite(url):
        # Ensure the parent directory exists; the default lives in data/.
        _, _, path = url.partition(":///")
        if path and not path.startswith(":memory:"):
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(
        url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        # aiosqlite is single-writer; the default pool is fine and avoids
        # "database is locked" under the app's modest concurrency.
        connect_args={"timeout": 30} if _is_sqlite(url) else {},
    )

    if _is_sqlite(url):

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    return engine


def get_engine(settings: Settings) -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine(settings)
    return _engine


def get_sessionmaker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(settings), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


async def create_all(engine: AsyncEngine) -> None:
    """Create tables directly. Used by tests; production uses Alembic."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def session_scope(settings: Settings) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that rolls back on error."""
    factory = get_sessionmaker(settings)
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

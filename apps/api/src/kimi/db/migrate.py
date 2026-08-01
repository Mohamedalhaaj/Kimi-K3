"""Running Alembic migrations from the application.

The API used to call ``Base.metadata.create_all`` on boot. That creates missing
*tables* but never missing *columns*, so adding a field to a model left an
existing local database silently stale — the app started cleanly and then failed
at query time with ``no such column``. Migrations are the fix, and running them
on boot keeps the one-command local start honest.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config

log = structlog.get_logger(__name__)

#: apps/api/, which holds alembic.ini and alembic/.
API_ROOT = Path(__file__).resolve().parents[3]


def alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    # Passed explicitly rather than read from the cached global settings, so a
    # test (or a second database) migrates the database it actually uses.
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _upgrade(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")


async def upgrade_to_head(database_url: str) -> None:
    """Apply pending migrations to ``database_url``.

    Alembic's env.py drives its own event loop, so it runs in a worker thread
    rather than inside the caller's running loop.
    """
    await asyncio.to_thread(_upgrade, database_url)
    log.info("db.migrated")

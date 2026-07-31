"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from kimi.config import Settings, get_settings
from kimi.db.session import session_scope
from kimi.errors import ModelError
from kimi.providers.base import ChatProvider
from kimi.providers.tokenrouter import TokenRouterProvider

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_session(settings: SettingsDep) -> AsyncIterator[AsyncSession]:
    async for session in session_scope(settings):
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]

_provider: ChatProvider | None = None


def get_provider(settings: SettingsDep) -> ChatProvider:
    """Return the configured chat provider.

    Raises a typed error — not a 500 — when no credential is configured, so the
    UI can show an actionable message instead of a stack trace.
    """
    global _provider
    if not settings.has_model_provider:
        raise ModelError(
            "No model provider is configured. Add TOKENROUTER_API_KEY to your .env "
            "and restart the API.",
            detail="settings.has_model_provider is False",
        )
    if _provider is None:
        assert settings.tokenrouter_api_key is not None
        _provider = TokenRouterProvider(
            api_key=settings.tokenrouter_api_key.get_secret_value(),
            base_url=settings.tokenrouter_base_url,
            request_timeout_s=settings.request_timeout_s,
            connect_timeout_s=settings.connect_timeout_s,
            max_retries=settings.max_retries,
        )
    return _provider


def reset_provider() -> None:
    """Test hook: drop the memoised provider."""
    global _provider
    _provider = None


ProviderDep = Annotated[ChatProvider, Depends(get_provider)]

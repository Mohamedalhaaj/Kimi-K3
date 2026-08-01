"""Liveness and readiness.

``/healthz`` answers "is the process up" and must never touch a dependency.
``/readyz`` answers "can this instance actually serve a chat turn" and therefore
does check the database and whether a provider credential is configured. Keeping
them separate stops a transient DB blip from making a load balancer kill a
perfectly healthy process.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from kimi.deps import SessionDep, SettingsDep
from kimi.providers.tokenrouter import TokenRouterProvider, known_models

log = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(session: SessionDep, settings: SettingsDep, response: Response) -> dict[str, Any]:
    checks: dict[str, Any] = {}

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": type(exc).__name__}

    checks["model_provider"] = {
        "ok": settings.has_model_provider,
        "configured": settings.has_model_provider,
    }

    ready = all(c["ok"] for c in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": ready, "checks": checks, "environment": settings.environment}


@router.get("/models")
async def list_models(settings: SettingsDep) -> dict[str, Any]:
    """Advertise model capabilities so the UI never offers an impossible action.

    This reads the static capability registry and does not require a credential,
    so the model picker still renders before the key is configured.
    """
    models = known_models()
    source = "registry"

    # Prefer the provider's own list so the picker never offers a model this
    # key cannot call. Falling back is safe: the request will surface a clear
    # auth error rather than a silent failure.
    if settings.has_model_provider and settings.tokenrouter_api_key is not None:
        provider = TokenRouterProvider(
            api_key=settings.tokenrouter_api_key.get_secret_value(),
            base_url=settings.tokenrouter_base_url,
            request_timeout_s=10.0,
            max_retries=0,
        )
        try:
            available = await provider.fetch_available_models()
            if available:
                models = tuple(available)
                source = "provider"
        except Exception as exc:
            log.warning("models.discovery_failed", exc_type=type(exc).__name__)
        finally:
            await provider.aclose()

    ids = {m.id for m in models}
    default = (
        settings.default_model
        if settings.default_model in ids
        else next(iter(m.id for m in models), settings.default_model)
    )
    return {
        "default": default,
        "source": source,
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "capabilities": sorted(str(c) for c in m.capabilities),
                "context_window": m.context_window,
            }
            for m in models
        ],
    }

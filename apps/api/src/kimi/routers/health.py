"""Liveness and readiness.

``/healthz`` answers "is the process up" and must never touch a dependency.
``/readyz`` answers "can this instance actually serve a chat turn" and therefore
does check the database and whether a provider credential is configured. Keeping
them separate stops a transient DB blip from making a load balancer kill a
perfectly healthy process.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from kimi.deps import SessionDep, SettingsDep
from kimi.providers.tokenrouter import known_models

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
    return {
        "default": settings.default_model,
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "capabilities": sorted(str(c) for c in m.capabilities),
                "context_window": m.context_window,
            }
            for m in known_models()
        ],
    }

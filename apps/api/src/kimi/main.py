"""FastAPI application factory."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kimi.config import Settings, get_settings
from kimi.db.migrate import upgrade_to_head
from kimi.db.session import create_all, dispose, get_engine
from kimi.errors import ErrorCode, KimiError
from kimi.logging import configure_logging
from kimi.routers import chat, conversations, files, health, tools
from kimi.tools.builtin import register_builtin_tools

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(level=settings.log_level, json_output=settings.environment == "production")
    engine = get_engine(settings)
    # Migrations, not create_all: create_all adds missing tables but never
    # missing columns, which left stale local databases failing at query time.
    try:
        await upgrade_to_head(settings.database_url)
    except Exception as exc:
        log.warning("db.migration_failed", exc_type=type(exc).__name__)
        await create_all(engine)
    log.info(
        "api.startup",
        environment=settings.environment,
        provider_configured=settings.has_model_provider,
    )
    try:
        yield
    finally:
        await dispose()
        log.info("api.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Kimi Workspace 2 API",
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment == "local" else None,
        redoc_url=None,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def correlate(
        request: Request, call_next: Callable[[Request], Awaitable[object]]
    ) -> object:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        elapsed_ms = (time.perf_counter() - started) * 1000
        # Path only — query strings can carry tokens and are never logged.
        log.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=getattr(response, "status_code", 0),
            duration_ms=round(elapsed_ms, 1),
            request_id=request_id,
        )
        if hasattr(response, "headers"):
            response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(KimiError)
    async def kimi_error_handler(request: Request, exc: KimiError) -> JSONResponse:
        cfg: Settings = request.app.state.settings
        log.warning("app.error", code=str(exc.code), detail=exc.detail, path=request.url.path)
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": exc.to_payload(include_detail=cfg.debug)},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": str(ErrorCode.INVALID_REQUEST),
                    "message": "That request was not valid.",
                    "retryable": False,
                    "fields": [
                        {"loc": list(e.get("loc", ())), "msg": e.get("msg", "")}
                        for e in exc.errors()
                    ],
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        cfg: Settings = request.app.state.settings
        # Log the type, never the message: raw exception text has leaked
        # internal hostnames and file paths in this codebase before.
        log.error("app.unhandled", exc_type=type(exc).__name__, path=request.url.path)
        payload = {
            "code": str(ErrorCode.INTERNAL),
            "message": "Something went wrong on our side.",
            "retryable": True,
        }
        if cfg.debug:
            payload["detail"] = f"{type(exc).__name__}: {exc}"
        return JSONResponse(status_code=500, content={"error": payload})

    register_builtin_tools()

    app.include_router(health.router)
    app.include_router(conversations.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(tools.router, prefix="/api")
    app.include_router(files.router, prefix="/api")
    return app


app = create_app()

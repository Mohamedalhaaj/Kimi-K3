"""Structured logging.

Every log line is JSON in production and human-readable in local mode. A
per-request correlation id is bound into the context so that a user-visible
error can be traced to exactly one request without logging any of its content.

Nothing here ever logs message bodies, uploaded file contents, cookies, or
Authorization headers. :func:`redact` exists so that call sites which must log a
URL or a header bag cannot leak a credential by accident.
"""

from __future__ import annotations

import logging
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import structlog

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "cookie",
        "set-cookie",
        "x-api-key",
        "tokenrouter_api_key",
    }
)

_REDACTED = "«redacted»"


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Copy ``data`` with any sensitive-looking key replaced."""
    return {k: (_REDACTED if k.lower() in _SENSITIVE_KEYS else v) for k, v in data.items()}


def redact_url(url: str) -> str:
    """Strip userinfo and the query string from a URL before logging it.

    Query strings routinely carry signed-URL tokens and session ids, so the
    whole query is dropped rather than selectively filtered.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return _REDACTED
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> None:
    """Install the structlog + stdlib pipeline. Safe to call more than once."""
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[*shared, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level.upper())
    # uvicorn's own access log duplicates our request middleware.
    logging.getLogger("uvicorn.access").disabled = True

"""Registers the built-in tools.

Importing this module populates the process-wide registry. It is imported once,
by the app factory. Registration is explicit and ordered here rather than by
import side effects scattered across modules — the failure mode the audit
recorded for ``core/__init__.py``, whose correctness depended on statement
ordering enforced only by a comment.
"""

from __future__ import annotations

from kimi.tools.browser import BROWSER_TOOLS
from kimi.tools.calculator import CALCULATOR
from kimi.tools.registry import registry
from kimi.tools.web import NEWS_SEARCH, OPEN_PUBLIC_URL, READ_ARTICLE, WEB_SEARCH

_REGISTERED = False


def register_builtin_tools() -> None:
    """Idempotent: safe to call from both the app factory and tests."""
    global _REGISTERED
    if _REGISTERED:
        return
    for spec in (
        CALCULATOR,
        OPEN_PUBLIC_URL,
        READ_ARTICLE,
        WEB_SEARCH,
        NEWS_SEARCH,
        *BROWSER_TOOLS,
    ):
        if spec.id not in registry:
            registry.register(spec)
    _REGISTERED = True


register_builtin_tools()

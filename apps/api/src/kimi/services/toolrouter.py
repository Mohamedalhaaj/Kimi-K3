"""Deciding which tool a message needs.

This is deliberately a *deterministic* pre-router, not a model call. Choosing a
tool must not itself cost a token, and the choice must be testable.

It is also deliberately conservative. The prototype's ``should_browse`` matched
unbounded substrings over a list containing "search", "web", "open ", "today"
and the two-character Arabic token "زر", so "How do I open a file in Python?"
triggered a live multi-backend web search (AUDIT §5, web_tools.py:157).
Here a search runs only on an explicit command, an explicit URL, or a clear
news/lookup phrasing — and the caller can always force or forbid it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from kimi.research.query import Intent, classify_intent, extract_topic, extract_urls


class ResearchMode(StrEnum):
    OFF = "off"
    AUTO = "auto"
    ALWAYS = "always"


#: `/calc 2+2` and friends.
_CALC_COMMAND: Final = re.compile(r"^\s*/(?:calc|calculate)\s+(?P<expr>.+)$", re.IGNORECASE | re.S)

#: A message that is *only* arithmetic, e.g. "25*4" or "(2+3)/4".
_BARE_ARITHMETIC: Final = re.compile(
    r"^\s*[\d\s+\-*/%().,^]+\s*$|"
    r"^\s*(?:[\d.]+|pi|e|tau|sqrt|abs|round|log|log10|sin|cos|tan|ceil|floor|[\s+\-*/%().,^])+\s*$",
    re.IGNORECASE,
)

_CALC_PHRASE: Final = re.compile(
    r"^\s*(?:what(?:'s| is)|calculate|compute|work out|how much is)\s+"
    r"(?P<expr>[\d\s+\-*/%().,^]+(?:[a-z0-9_]*\([^)]*\))?[\d\s+\-*/%().,^]*)\s*[?.]?\s*$",
    re.IGNORECASE,
)

_EXPLICIT_SEARCH: Final = re.compile(
    r"^\s*/(?:search|news|web)\s+(?P<query>.+)$", re.IGNORECASE | re.S
)
_EXPLICIT_OPEN: Final = re.compile(r"^\s*/(?:open|read)\s+(?P<url>\S+)", re.IGNORECASE)


@dataclass(slots=True)
class ToolPlan:
    """Which tool to run, if any."""

    tool_id: str | None
    arguments: dict[str, Any]
    #: True when the tool's own output is the whole answer.
    deterministic: bool = False
    reason: str = ""


def _looks_like_arithmetic(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 200:
        return False
    if not any(ch.isdigit() for ch in stripped):
        return False
    if not any(op in stripped for op in "+-*/%^"):
        return False
    return bool(_BARE_ARITHMETIC.match(stripped))


def route(text: str, *, research: ResearchMode = ResearchMode.AUTO) -> ToolPlan:
    """Pick a tool for ``text``. Returns ``tool_id=None`` for a plain chat turn."""
    message = (text or "").strip()
    if not message:
        return ToolPlan(None, {}, reason="empty message")

    # ---- explicit commands first ---------------------------------------
    if match := _CALC_COMMAND.match(message):
        return ToolPlan(
            "calculator",
            {"expression": match.group("expr").strip()},
            deterministic=True,
            reason="/calc command",
        )

    if match := _EXPLICIT_OPEN.match(message):
        return ToolPlan("open_public_url", {"url": match.group("url")}, reason="/open command")

    if match := _EXPLICIT_SEARCH.match(message):
        query = match.group("query").strip()
        tool = "news_search" if classify_intent(query) is Intent.NEWS else "web_search"
        return ToolPlan(tool, {"query": query}, reason="/search command")

    # ---- deterministic arithmetic --------------------------------------
    if _looks_like_arithmetic(message):
        return ToolPlan(
            "calculator", {"expression": message}, deterministic=True, reason="bare arithmetic"
        )
    if (match := _CALC_PHRASE.match(message)) and _looks_like_arithmetic(match.group("expr")):
        return ToolPlan(
            "calculator",
            {"expression": match.group("expr").strip()},
            deterministic=True,
            reason="calculation phrasing",
        )

    if research is ResearchMode.OFF:
        return ToolPlan(None, {}, reason="research disabled")

    # ---- an explicit URL is an unambiguous instruction ------------------
    urls = extract_urls(message)
    if urls:
        return ToolPlan("read_article", {"url": urls[0]}, reason="explicit URL")

    intent = classify_intent(message)
    if intent is Intent.NEWS:
        return ToolPlan("news_search", {"query": message}, reason="news intent")

    if research is ResearchMode.ALWAYS:
        topic = extract_topic(message)
        return ToolPlan("web_search", {"query": topic or message}, reason="research forced on")

    return ToolPlan(None, {}, reason="no research signal")

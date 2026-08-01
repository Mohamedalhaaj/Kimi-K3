from __future__ import annotations

import re
from typing import Any


_BROWSER_COMMAND = re.compile(
    r"^\s*/browser\s+(open|inspect|links|click|type|back|reload)\b",
    re.IGNORECASE,
)

_ACTION_LIMITS = {
    "open": 4_000,
    "inspect": 7_000,
    "links": 6_000,
    "click": 3_500,
    "type": 2_500,
    "back": 3_500,
    "reload": 3_500,
}


def _compact_text(value: str, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[Page output truncated for speed.]"


def _compact_browser_result(result: Any, action: str) -> Any:
    limit = _ACTION_LIMITS.get(action, 3_500)

    for source in getattr(result, "sources", []) or []:
        content = getattr(source, "content", "") or ""
        if content:
            source.content = _compact_text(content, min(limit, 4_000))

    original_context = getattr(result, "context", "") or ""
    compact_context = _compact_text(original_context, limit)

    instruction = (
        "\n\nBROWSER RESPONSE INSTRUCTION\n"
        "The browser action has already completed. Reply immediately and briefly. "
        "Confirm the resulting page and URL. Use at most 80 words unless the user "
        "explicitly requested inspection or links. Do not repeat the full page text, "
        "all controls, or all links. Do not claim the browser action is still running."
    )
    result.context = compact_context + instruction

    events = getattr(result, "events", None)
    if isinstance(events, list):
        events.append("Compacted rendered page context for a faster response")

    return result


def install_browser_speed_patch(web_tools_module: Any) -> None:
    """Reduce the prompt size produced by explicit Playwright browser commands."""
    original_browse = web_tools_module.browse_web

    def browse_with_compact_browser_results(
        query: str,
        mode: str,
        depth: str,
        max_results: int = 5,
        max_context_chars: int = 30_000,
    ):
        result = original_browse(
            query=query,
            mode=mode,
            depth=depth,
            max_results=max_results,
            max_context_chars=max_context_chars,
        )

        match = _BROWSER_COMMAND.match(query or "")
        if not match:
            return result

        return _compact_browser_result(result, match.group(1).casefold())

    web_tools_module.browse_web = browse_with_compact_browser_results

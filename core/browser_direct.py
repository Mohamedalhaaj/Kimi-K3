from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Iterable


_BROWSER_COMMAND = re.compile(
    r"^\s*/browser\s+(open|inspect|links|click|type|back|reload)\b(?:\s+(.*?))?(?:\n|$)",
    re.IGNORECASE | re.DOTALL,
)


def _last_user_text(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""

    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and item.get("text"):
                    text_parts.append(str(item["text"]))
            return "\n".join(text_parts)
    return ""


def _field(text: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _section(text: str, heading: str, limit: int) -> str:
    pattern = rf"{re.escape(heading)}:\s*\n(.*?)(?:\n\n[A-Z][A-Z /]+:\s*\n|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return ""
    value = match.group(1).strip()
    if len(value) > limit:
        return value[:limit].rstrip() + "…"
    return value


def _markdown_url(title: str, url: str) -> str:
    if title and url:
        return f"**{title}**\n\n{url}"
    if url:
        return url
    return f"**{title}**" if title else "the current page"


def _direct_browser_response(text: str) -> str | None:
    match = _BROWSER_COMMAND.match(text or "")
    if not match:
        return None

    action = match.group(1).casefold()
    argument = (match.group(2) or "").splitlines()[0].strip()
    title = _field(text, "Title")
    url = _field(text, "URL")
    reason = _field(text, "Reason")

    if reason or "The requested browser action was not completed" in text:
        return f"Browser action failed: {reason or 'the page could not be controlled.'}"

    page = _markdown_url(title, url)

    if action == "open":
        return f"Opened {page}"
    if action == "click":
        target = f" **{argument}**" if argument else ""
        return f"Clicked{target}. Current page: {page}"
    if action == "back":
        return f"Went back. Current page: {page}"
    if action == "reload":
        return f"Reloaded {page}"
    if action == "type":
        label = argument.split("::", 1)[0].strip() if "::" in argument else argument
        field_label = f" **{label}**" if label else ""
        return f"Entered the text in{field_label}. Current page: {page}"
    if action == "links":
        links = _section(text, "VISIBLE LINKS", 5_500)
        if links:
            return f"Visible links on {page}:\n\n{links}"
        return f"No visible links were captured on {page}."
    if action == "inspect":
        visible_text = _section(text, "VISIBLE PAGE TEXT", 2_500)
        controls = _section(text, "VISIBLE BUTTONS/CONTROLS", 1_200)
        parts = [f"Inspected {page}."]
        if visible_text:
            parts.append(visible_text)
        if controls:
            parts.append("**Visible controls**\n\n" + controls)
        return "\n\n".join(parts)

    return f"Browser action completed on {page}."


def _stream_text(text: str) -> Iterable[Any]:
    yield SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))],
        usage=None,
    )


def install_direct_browser_response_patch() -> None:
    """Intercept explicit /browser commands before they reach TokenRouter."""
    try:
        from openai import OpenAI
    except Exception:
        return

    if getattr(OpenAI, "_kimi_direct_browser_init_patch", False):
        return

    original_init = OpenAI.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)

        completions = self.chat.completions
        completions_class = type(completions)
        if getattr(completions_class, "_kimi_direct_browser_create_patch", False):
            return

        original_create = completions_class.create

        def patched_create(resource_self: Any, *call_args: Any, **call_kwargs: Any) -> Any:
            direct_text = _direct_browser_response(
                _last_user_text(call_kwargs.get("messages"))
            )
            if direct_text is None:
                return original_create(resource_self, *call_args, **call_kwargs)

            if call_kwargs.get("stream"):
                return _stream_text(direct_text)

            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=direct_text),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )

        completions_class.create = patched_create
        completions_class._kimi_direct_browser_create_patch = True

    OpenAI.__init__ = patched_init
    OpenAI._kimi_direct_browser_init_patch = True

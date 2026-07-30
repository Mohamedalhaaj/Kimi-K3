from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_BROWSER_COMMAND = re.compile(
    r"^\s*/browser\s+(open|inspect|links|click|type|back|reload)\b(?:\s+(.*))?$",
    re.IGNORECASE | re.DOTALL,
)
_DANGEROUS_CLICK_TERMS = (
    "buy",
    "purchase",
    "checkout",
    "pay",
    "delete",
    "remove account",
    "transfer",
    "send money",
    "place order",
    "confirm order",
    "publish",
    "post now",
    "submit application",
)
_SENSITIVE_FIELD_TERMS = (
    "password",
    "passcode",
    "pin",
    "otp",
    "verification code",
    "security code",
    "cvv",
    "card number",
    "credit card",
    "bank account",
    "routing number",
    "social security",
)
_MAX_PAGE_TEXT = 24_000
_MAX_LINKS = 45
_STATE_DIR = Path.home() / ".kimi-workspace"
_STATE_FILE = _STATE_DIR / "browser-state.json"
_PROFILE_DIR = _STATE_DIR / "browser-profile"
_LAST_SCREENSHOT = _STATE_DIR / "browser-last.png"


def _load_state() -> dict[str, Any]:
    try:
        value = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value.setdefault("history", [])
            return value
    except (OSError, json.JSONDecodeError):
        pass
    return {"current_url": "", "history": []}


def _save_state(state: dict[str, Any]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normalize_public_url(value: str, is_public_web_url: Any) -> str:
    url = (value or "").strip()
    if not url:
        raise ValueError("A public URL is required.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not is_public_web_url(url):
        raise ValueError("Only public HTTP/HTTPS URLs are permitted.")
    return url


def _is_headless() -> bool:
    value = os.getenv("KIMI_BROWSER_HEADLESS", "true").strip().casefold()
    return value not in {"0", "false", "no", "off"}


def _safe_click_target(value: str) -> None:
    lowered = (value or "").casefold()
    if any(term in lowered for term in _DANGEROUS_CLICK_TERMS):
        raise ValueError(
            "This click may create a purchase, deletion, publication, payment, "
            "or other consequential action. The browser agent will not execute it."
        )


def _safe_field_label(label: str) -> None:
    lowered = (label or "").casefold()
    if any(term in lowered for term in _SENSITIVE_FIELD_TERMS):
        raise ValueError(
            "The browser agent will not type passwords, PINs, payment details, "
            "one-time codes, or other sensitive credentials."
        )


def _launch_context(playwright: Any):
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(_PROFILE_DIR),
        headless=_is_headless(),
        viewport={"width": 1440, "height": 1000},
        locale="en-US",
        accept_downloads=False,
        args=["--disable-dev-shm-usage"],
    )


def _settle(page: Any) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=20_000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=5_000)
    except Exception:
        pass


def _navigate(page: Any, url: str, is_public_web_url: Any) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=35_000)
    _settle(page)
    if not is_public_web_url(page.url):
        raise ValueError("The page redirected to a non-public address.")


def _extract_page(page: Any) -> tuple[str, str, list[str], list[str]]:
    title = page.title().strip() or urlparse(page.url).netloc
    try:
        text = page.locator("body").inner_text(timeout=12_000).strip()
    except Exception:
        text = ""
    text = text[:_MAX_PAGE_TEXT]

    try:
        links_raw = page.locator("a:visible").evaluate_all(
            """els => els.slice(0, 80).map(el => ({
                text: (el.innerText || el.getAttribute('aria-label') || '').trim(),
                href: el.href || ''
            }))"""
        )
    except Exception:
        links_raw = []

    links: list[str] = []
    for item in links_raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("text") or "").strip()
        href = str(item.get("href") or "").strip()
        if not href:
            continue
        line = f"{label or '[untitled link]'} — {href}"
        if line not in links:
            links.append(line)
        if len(links) >= _MAX_LINKS:
            break

    try:
        controls_raw = page.locator(
            "button:visible, input[type=submit]:visible, input[type=button]:visible"
        ).evaluate_all(
            """els => els.slice(0, 60).map(el =>
                (el.innerText || el.value || el.getAttribute('aria-label') || '').trim()
            ).filter(Boolean)"""
        )
    except Exception:
        controls_raw = []

    controls: list[str] = []
    for value in controls_raw:
        label = str(value or "").strip()
        if label and label not in controls:
            controls.append(label)
        if len(controls) >= 35:
            break

    return title, text, links, controls


def _click(page: Any, target: str) -> None:
    _safe_click_target(target)
    candidates = (
        page.get_by_role("link", name=target, exact=False),
        page.get_by_role("button", name=target, exact=False),
        page.get_by_text(target, exact=False),
    )
    last_error: Exception | None = None
    for locator in candidates:
        try:
            if locator.count() > 0:
                locator.first.click(timeout=12_000)
                _settle(page)
                return
        except Exception as exc:
            last_error = exc
    raise ValueError(
        f'No visible link, button, or text matching "{target}" was clickable.'
    ) from last_error


def _type(page: Any, payload: str) -> None:
    if "::" not in payload:
        raise ValueError('Use `/browser type Field label :: text to enter`.')
    label, value = [part.strip() for part in payload.split("::", 1)]
    if not label:
        raise ValueError("A field label is required.")
    _safe_field_label(label)

    candidates = (
        page.get_by_label(label, exact=False),
        page.get_by_placeholder(label, exact=False),
        page.get_by_role("textbox", name=label, exact=False),
    )
    last_error: Exception | None = None
    for locator in candidates:
        try:
            if locator.count() > 0:
                locator.first.fill(value, timeout=12_000)
                return
        except Exception as exc:
            last_error = exc
    raise ValueError(f'No editable field matching "{label}" was found.') from last_error


def _build_context(
    action: str,
    title: str,
    url: str,
    text: str,
    links: list[str],
    controls: list[str],
) -> str:
    sections = [
        "LOCAL PLAYWRIGHT BROWSER RESULT",
        f"Action: {action}",
        f"Title: {title}",
        f"URL: {url}",
        (
            "This content was rendered in an isolated local Chromium browser. "
            "It is separate from the user's existing Chrome window."
        ),
    ]
    if text:
        sections.append("VISIBLE PAGE TEXT:\n" + text)
    if controls:
        sections.append(
            "VISIBLE BUTTONS/CONTROLS:\n"
            + "\n".join(f"- {item}" for item in controls)
        )
    if links:
        sections.append(
            "VISIBLE LINKS:\n"
            + "\n".join(f"- {item}" for item in links)
        )
    return "\n\n".join(sections)


def run_browser_command(
    query: str,
    *,
    ToolResult: Any,
    Source: Any,
    is_public_web_url: Any,
) -> Any | None:
    match = _BROWSER_COMMAND.match(query or "")
    if not match:
        return None

    action = match.group(1).casefold()
    argument = (match.group(2) or "").strip()
    result = ToolResult()

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        result.warnings.append(
            "Playwright is not installed. Run `python -m pip install playwright` "
            "and `python -m playwright install chromium`."
        )
        result.context = "LOCAL BROWSER TOOL STATUS\n" + result.warnings[0]
        return result

    state = _load_state()
    current_url = str(state.get("current_url") or "")
    history = [
        str(item) for item in state.get("history", [])
        if isinstance(item, str)
    ]

    try:
        if action == "open":
            target_url = _normalize_public_url(argument, is_public_web_url)
        elif action == "back":
            if len(history) < 2:
                raise ValueError("There is no previous browser page in history.")
            history.pop()
            target_url = _normalize_public_url(history[-1], is_public_web_url)
        else:
            target_url = _normalize_public_url(current_url, is_public_web_url)

        with sync_playwright() as playwright:
            context = _launch_context(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                _navigate(page, target_url, is_public_web_url)

                if action == "click":
                    if not argument:
                        raise ValueError("Provide visible link or button text to click.")
                    _click(page, argument)
                elif action == "type":
                    _type(page, argument)
                elif action == "reload":
                    page.reload(wait_until="domcontentloaded", timeout=35_000)
                    _settle(page)
                elif action in {"inspect", "links", "open", "back"}:
                    pass
                else:
                    raise ValueError(f"Unsupported browser action: {action}")

                final_url = _normalize_public_url(page.url, is_public_web_url)
                title, text, links, controls = _extract_page(page)
                try:
                    page.screenshot(path=str(_LAST_SCREENSHOT), full_page=False)
                except Exception:
                    pass
            finally:
                context.close()

        if not history or history[-1] != final_url:
            history.append(final_url)
        state = {"current_url": final_url, "history": history[-30:]}
        _save_state(state)

        if action == "links":
            text = ""
            controls = []

        result.context = _build_context(
            action=action,
            title=title,
            url=final_url,
            text=text,
            links=links,
            controls=controls,
        )
        result.sources.append(
            Source(
                title=title,
                url=final_url,
                snippet="Rendered with the isolated local Playwright browser.",
                content=text,
                source_type="playwright-browser",
            )
        )
        result.events.append(f"Browser {action}: {title}")
        if _LAST_SCREENSHOT.exists():
            result.events.append(f"Browser screenshot saved to {_LAST_SCREENSHOT}")
        return result

    except Exception as exc:
        result.warnings.append(f"Browser action failed: {exc}")
        result.context = (
            "LOCAL BROWSER TOOL STATUS\n"
            "The requested browser action was not completed.\n"
            f"Reason: {exc}"
        )
        return result


def render_public_page(
    url: str,
    *,
    is_public_web_url: Any,
    max_chars: int = _MAX_PAGE_TEXT,
) -> tuple[str, str, str]:
    """Render a public page in Chromium and return title, visible text, final URL."""
    normalized = _normalize_public_url(url, is_public_web_url)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(
            "Playwright is not installed or Chromium is unavailable."
        ) from exc

    with sync_playwright() as playwright:
        context = _launch_context(playwright)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            _navigate(page, normalized, is_public_web_url)
            title, text, _, _ = _extract_page(page)
            final_url = _normalize_public_url(page.url, is_public_web_url)
            return title, text[:max_chars], final_url
        finally:
            context.close()


def install_browser_patches(web_tools_module: Any) -> None:
    """Patch web_tools with browser commands and a Chromium fallback reader."""
    original_fetch = web_tools_module.fetch_public_page
    original_browse = web_tools_module.browse_web

    def fetch_with_browser_fallback(
        url: str,
        timeout_seconds: int = 15,
    ) -> tuple[str, str, str]:
        try:
            return original_fetch(url, timeout_seconds)
        except Exception as first_error:
            try:
                return render_public_page(
                    url,
                    is_public_web_url=web_tools_module.is_public_web_url,
                )
            except Exception as browser_error:
                raise ValueError(
                    f"HTTP/Jina reader failed ({first_error}); "
                    f"Playwright renderer failed ({browser_error})."
                ) from browser_error

    def browse_with_browser_commands(
        query: str,
        mode: str,
        depth: str,
        max_results: int = 5,
        max_context_chars: int = 30_000,
    ):
        command_result = run_browser_command(
            query,
            ToolResult=web_tools_module.ToolResult,
            Source=web_tools_module.Source,
            is_public_web_url=web_tools_module.is_public_web_url,
        )
        if command_result is not None:
            return command_result
        return original_browse(
            query=query,
            mode=mode,
            depth=depth,
            max_results=max_results,
            max_context_chars=max_context_chars,
        )

    web_tools_module.fetch_public_page = fetch_with_browser_fallback
    web_tools_module.browse_web = browse_with_browser_commands

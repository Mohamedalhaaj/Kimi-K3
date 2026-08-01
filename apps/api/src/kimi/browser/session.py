"""A persistent, isolated Chromium session.

Three structural fixes over the prototype (AUDIT §5, browser_agent.py:45-47,
310-337):

* **The browser stays open.** The prototype entered ``sync_playwright()`` and
  called ``context.close()`` per command, so every click paid a Chromium cold
  start plus a full page load.
* **No unconditional re-navigation.** ``_navigate`` ran before every click and
  type, which discarded the page state the previous step had just created —
  making multi-step interaction structurally impossible even though the README
  documented exactly that sequence.
* **Per-installation profile, not machine-global.** ``Path.home()/".kimi-workspace"``
  meant one cookie jar shared by every user of a deployment, and Chromium takes
  an exclusive lock on it so a second concurrent action simply failed.

The profile lives under the repository's gitignored ``data/`` directory.
"""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import structlog

from kimi.config import REPO_ROOT
from kimi.research.net import UnsafeUrlError, validate_url

log = structlog.get_logger(__name__)

PROFILE_DIR: Final = REPO_ROOT / "data" / "browser-profile"
#: Bounded so a page that never goes idle cannot hang a turn. The prototype
#: waited on networkidle, which some sites never reach.
NAV_TIMEOUT_MS: Final = 20_000
ACTION_TIMEOUT_MS: Final = 8_000
SETTLE_MS: Final = 900
MAX_HISTORY: Final = 50


class BrowserUnavailableError(Exception):
    """Playwright or its browsers are not installed. Message is user-safe."""


@dataclass(slots=True)
class PageState:
    url: str = "about:blank"
    title: str = ""
    history: list[str] = field(default_factory=list)
    screenshot_data_url: str = ""
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_payload(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "history": self.history[-MAX_HISTORY:],
            "screenshot": self.screenshot_data_url,
            "updated_at": self.updated_at.isoformat(),
        }


class BrowserSession:
    """One long-lived Chromium context, guarded by a lock.

    The lock serialises actions: Chromium holds an exclusive lock on the user
    data directory, and two concurrent actions on one page would race anyway.
    """

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self._lock = asyncio.Lock()
        self.state = PageState()
        self._history: list[str] = []

    # -- lifecycle ------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._context is not None

    async def _ensure(self) -> Any:
        if self._page is not None:
            return self._page

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserUnavailableError(
                "The browser agent needs Playwright. Install it with "
                "`uv sync --extra browser` in apps/api."
            ) from exc

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=self._headless,
                # Downloads are never accepted: an agent that can write files to
                # disk is a different threat model than one that reads pages.
                accept_downloads=False,
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:
            await self.close()
            raise BrowserUnavailableError(
                "Chromium could not start. Run `uv run playwright install chromium` in apps/api."
            ) from exc

        self._page = (
            self._context.pages[0] if self._context.pages else await self._context.new_page()
        )
        self._page.set_default_timeout(ACTION_TIMEOUT_MS)
        self._page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        return self._page

    async def close(self) -> None:
        for closer in (self._context, self._playwright):
            if closer is None:
                continue
            try:
                await (closer.close() if hasattr(closer, "close") else closer.stop())
            except Exception as exc:
                log.warning("browser.close_failed", exc_type=type(exc).__name__)
        self._context = None
        self._playwright = None
        self._page = None
        self.state = PageState()

    # -- helpers --------------------------------------------------------

    async def _settle(self, page: Any) -> None:
        """Two-stage bounded wait. Never networkidle — some pages never idle."""
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception:
            log.info("browser.settle_timeout", stage="domcontentloaded")
        await asyncio.sleep(SETTLE_MS / 1000)

    async def _capture(self, page: Any, *, screenshot: bool = True) -> PageState:
        try:
            title = await page.title()
        except Exception:
            title = ""
        url = page.url
        if url and (not self._history or self._history[-1] != url):
            self._history.append(url)

        shot = ""
        if screenshot:
            try:
                raw = await page.screenshot(type="jpeg", quality=60, full_page=False)
                shot = f"data:image/jpeg;base64,{base64.b64encode(raw).decode()}"
            except Exception as exc:
                log.info("browser.screenshot_failed", exc_type=type(exc).__name__)

        self.state = PageState(
            url=url,
            title=title,
            history=list(self._history[-MAX_HISTORY:]),
            screenshot_data_url=shot,
        )
        return self.state

    # -- actions --------------------------------------------------------

    async def open(self, url: str) -> PageState:
        """Navigate, re-validating the address against the SSRF policy."""
        try:
            validate_url(url)
        except UnsafeUrlError as exc:
            raise BrowserUnavailableError("Only public web addresses can be opened.") from exc

        async with self._lock:
            page = await self._ensure()
            started = time.perf_counter()
            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await self._settle(page)
            # The landing page may differ from the requested one after a
            # redirect chain, so the final URL is checked too.
            try:
                validate_url(page.url)
            except UnsafeUrlError as exc:
                await page.goto("about:blank")
                raise BrowserUnavailableError(
                    "That link redirected somewhere this agent will not follow."
                ) from exc
            state = await self._capture(page)
            log.info(
                "browser.open",
                host=_host(state.url),
                ms=round((time.perf_counter() - started) * 1000),
            )
            return state

    async def back(self) -> PageState:
        async with self._lock:
            page = await self._ensure()
            await page.go_back(timeout=NAV_TIMEOUT_MS)
            await self._settle(page)
            return await self._capture(page)

    async def forward(self) -> PageState:
        async with self._lock:
            page = await self._ensure()
            await page.go_forward(timeout=NAV_TIMEOUT_MS)
            await self._settle(page)
            return await self._capture(page)

    async def reload(self) -> PageState:
        async with self._lock:
            page = await self._ensure()
            await page.reload(timeout=NAV_TIMEOUT_MS)
            await self._settle(page)
            return await self._capture(page)

    async def links(self, limit: int = 40) -> list[dict[str, str]]:
        """Visible links, for the user to choose from."""
        async with self._lock:
            page = await self._ensure()
            rows = await page.eval_on_selector_all(
                "a[href]",
                """els => els
                    .filter(e => e.offsetParent !== null)
                    .map(e => ({ text: (e.innerText || '').trim().slice(0, 120), href: e.href }))
                    .filter(x => x.text && x.href.startsWith('http'))""",
            )
            seen: set[str] = set()
            out: list[dict[str, str]] = []
            for row in rows:
                if row["href"] in seen:
                    continue
                seen.add(row["href"])
                out.append(row)
                if len(out) >= limit:
                    break
            return out

    async def text(self, limit: int = 6000) -> str:
        async with self._lock:
            page = await self._ensure()
            try:
                body = await page.inner_text("body", timeout=ACTION_TIMEOUT_MS)
            except Exception as exc:
                log.info("browser.text_failed", exc_type=type(exc).__name__)
                return ""
            return str(body)[:limit]

    async def resolve_click_target(self, query: str) -> tuple[Any, str]:
        """Find the element and return it with its ACTUAL label.

        Returning the resolved label is what lets the safety layer classify the
        thing being clicked rather than the thing being asked for.
        """
        page = await self._ensure()
        # Accessibility-first: role-based locators before text matching.
        for locator in (
            page.get_by_role("button", name=query, exact=False),
            page.get_by_role("link", name=query, exact=False),
            page.get_by_text(query, exact=False),
        ):
            try:
                element = locator.first
                if await element.count() == 0:
                    continue
                label = (await element.inner_text(timeout=2000) or "").strip()
                return element, label or query
            except Exception as exc:
                # Each strategy is a best effort; log and try the next one
                # rather than swallowing silently as the prototype did.
                log.debug("browser.locator_miss", exc_type=type(exc).__name__)
                continue
        raise BrowserUnavailableError(f"Nothing on this page matches “{query}”.")

    async def click_element(self, element: Any) -> PageState:
        async with self._lock:
            page = await self._ensure()
            await element.click(timeout=ACTION_TIMEOUT_MS)
            await self._settle(page)
            return await self._capture(page)

    async def fill(self, field_query: str, value: str) -> PageState:
        """Fill a field. Deliberately never submits — no Enter, no form.submit()."""
        async with self._lock:
            page = await self._ensure()
            locator = page.get_by_label(field_query, exact=False)
            if await locator.count() == 0:
                locator = page.get_by_placeholder(field_query, exact=False)
            if await locator.count() == 0:
                raise BrowserUnavailableError(f"No field on this page matches “{field_query}”.")
            await locator.first.fill(value, timeout=ACTION_TIMEOUT_MS)
            return await self._capture(page)


def _host(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return str(urlparse(url).hostname or "")
    except ValueError:
        return ""


#: One session per process. The brief's target is a local, single-user app.
_session: BrowserSession | None = None


def get_session() -> BrowserSession:
    global _session
    if _session is None:
        _session = BrowserSession()
    return _session


async def close_session() -> None:
    global _session
    if _session is not None:
        await _session.close()
    _session = None

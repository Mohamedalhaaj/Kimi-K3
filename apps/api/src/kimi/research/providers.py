"""Search provider adapters.

Every provider implements :class:`SearchProvider`, so adding a paid API later
means adding one class and registering it — no change to the pipeline. Each is
wrapped in its own circuit breaker and rate limiter, and each returns a
:class:`SearchResults` that records whether it worked.

A provider that fails contributes ``ok=False`` and an empty list. It never
raises into the pipeline, because the brief requires that one failed provider
must not invalidate the successful ones.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from typing import Any, Final
from urllib.parse import quote_plus, urlparse

import structlog

from kimi.research.dates import FEED_DATE_FIELDS, parse_datetime
from kimi.research.models import ExtractionStatus, RetrievalMethod, SearchResults, Source
from kimi.research.net import SafeFetcher, UnsafeUrlError
from kimi.research.query import FreshnessWindow
from kimi.research.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    RateLimiter,
    with_backoff,
)

log = structlog.get_logger(__name__)

_ATOM: Final = "{http://www.w3.org/2005/Atom}"


def _publisher_from_url(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


def _strip_tags(text: str) -> str:
    import re

    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    cleaned = (
        cleaned.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    return re.sub(r"\s+", " ", cleaned).strip()


class SearchProvider(ABC):
    """One search backend."""

    id: str
    label: str
    supports_news: bool = True
    supports_web: bool = False

    def __init__(self, *, fetcher: SafeFetcher, min_interval_s: float = 0.0) -> None:
        self._fetcher = fetcher
        self._breaker = CircuitBreaker(name=self.id)
        self._limiter = RateLimiter(min_interval_s=min_interval_s)

    @abstractmethod
    async def _search(self, query: str, window: FreshnessWindow, limit: int) -> list[Source]:
        """Provider-specific work. May raise; the wrapper handles it."""

    async def search(
        self, query: str, window: FreshnessWindow, *, limit: int = 10
    ) -> SearchResults:
        """Run the provider, never raising into the pipeline."""
        started = time.perf_counter()
        try:
            self._breaker.check()
            await self._limiter.acquire()
            sources = await with_backoff(
                lambda: self._search(query, window, limit),
                attempts=2,
                retry_on=(UnsafeUrlError, OSError, ET.ParseError, ValueError),
            )
            self._breaker.record_success()
            return SearchResults(
                provider=self.id,
                sources=sources,
                ok=True,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        except CircuitOpenError:
            return SearchResults(
                provider=self.id,
                ok=False,
                error="temporarily unavailable after repeated failures",
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            self._breaker.record_failure()
            log.warning("provider.failed", provider=self.id, exc_type=type(exc).__name__)
            return SearchResults(
                provider=self.id,
                ok=False,
                # Deliberately generic: raw transport text leaks internals.
                error="provider request failed",
                duration_ms=(time.perf_counter() - started) * 1000,
            )


class _RssProvider(SearchProvider):
    """Shared RSS/Atom parsing.

    Reads every date field the audit found missing — Atom ``published``/
    ``updated`` and Dublin Core ``dc:date``, not just ``pubDate``.
    """

    async def _parse_feed(self, xml_text: str, limit: int) -> list[Source]:
        try:
            root = ET.fromstring(xml_text)  # noqa: S314 - size-capped upstream
        except ET.ParseError:
            return []

        items = root.findall(".//item") or root.findall(f".//{_ATOM}entry")
        sources: list[Source] = []

        for item in items[: limit * 2]:
            title = (item.findtext("title") or item.findtext(f"{_ATOM}title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not link:
                anchor = item.find(f"{_ATOM}link")
                if anchor is not None:
                    link = (anchor.get("href") or "").strip()
            if not title or not link:
                continue

            raw_date = ""
            for field_name in FEED_DATE_FIELDS:
                value = item.findtext(field_name)
                if value:
                    raw_date = value
                    break
            published, verified = parse_datetime(raw_date)

            description = (
                item.findtext("description")
                or item.findtext(f"{_ATOM}summary")
                or item.findtext(f"{_ATOM}content")
                or ""
            )
            publisher = (item.findtext("source") or "").strip() or _publisher_from_url(link)

            sources.append(
                Source(
                    url=link,
                    title=_strip_tags(title),
                    publisher=publisher,
                    snippet=_strip_tags(description)[:400],
                    published_at=published,
                    date_verified=verified,
                    provider=self.id,
                    retrieval=RetrievalMethod.PROVIDER_SNIPPET,
                    status=ExtractionStatus.SNIPPET_ONLY,
                )
            )
            if len(sources) >= limit:
                break
        return sources


class GoogleNewsRSS(_RssProvider):
    id = "google_news"
    label = "Google News"

    async def _search(self, query: str, window: FreshnessWindow, limit: int) -> list[Source]:
        # Google News RSS supports a when: operator; align it with the window so
        # the provider filters server-side too.
        when = ""
        if window.hours is not None:
            when = (
                f"+when:{window.hours}h" if window.hours <= 48 else f"+when:{window.hours // 24}d"
            )
        url = (
            f"https://news.google.com/rss/search?q={quote_plus(query)}{when}&hl=en&gl=US&ceid=US:en"
        )
        result = await self._fetcher.fetch(url, accept="application/rss+xml,application/xml")
        return await self._parse_feed(result.text, limit)


class GoogleNewsArabicRSS(GoogleNewsRSS):
    id = "google_news_ar"
    label = "Google News (Arabic)"

    async def _search(self, query: str, window: FreshnessWindow, limit: int) -> list[Source]:
        when = ""
        if window.hours is not None:
            when = (
                f"+when:{window.hours}h" if window.hours <= 48 else f"+when:{window.hours // 24}d"
            )
        url = (
            f"https://news.google.com/rss/search?q={quote_plus(query)}{when}&hl=ar&gl=EG&ceid=EG:ar"
        )
        result = await self._fetcher.fetch(url, accept="application/rss+xml,application/xml")
        return await self._parse_feed(result.text, limit)


class BingNewsRSS(_RssProvider):
    id = "bing_news"
    label = "Bing News"

    async def _search(self, query: str, window: FreshnessWindow, limit: int) -> list[Source]:
        # Bing's RSS has no freshness parameter; the pipeline's own cutoff is
        # what enforces the window for this provider. The prototype shipped the
        # query without noting that, so results silently escaped the window.
        url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=RSS&setmkt=en-US"
        result = await self._fetcher.fetch(url, accept="application/rss+xml,application/xml")
        return await self._parse_feed(result.text, limit)


class GDELTProvider(SearchProvider):
    """GDELT's document API. Rate-limited hard, so spaced and backed off."""

    id = "gdelt"
    label = "GDELT"

    def __init__(self, *, fetcher: SafeFetcher) -> None:
        super().__init__(fetcher=fetcher, min_interval_s=1.5)

    async def _search(self, query: str, window: FreshnessWindow, limit: int) -> list[Source]:
        timespan = ""
        if window.hours is not None:
            timespan = f"&timespan={min(window.hours, 24 * 30)}h"
        url = (
            "https://api.gdeltproject.org/api/v2/doc/doc"
            f"?query={quote_plus(query)}&mode=ArtList&format=json"
            f"&maxrecords={min(limit, 50)}{timespan}&sort=DateDesc"
        )
        result = await self._fetcher.fetch(url, accept="application/json")

        import json

        try:
            payload: dict[str, Any] = json.loads(result.text)
        except json.JSONDecodeError:
            return []

        sources: list[Source] = []
        for article in payload.get("articles", [])[:limit]:
            link = (article.get("url") or "").strip()
            title = (article.get("title") or "").strip()
            if not link or not title:
                continue
            published, verified = parse_datetime(article.get("seendate"))
            sources.append(
                Source(
                    url=link,
                    title=title,
                    publisher=(article.get("domain") or _publisher_from_url(link)),
                    snippet="",
                    published_at=published,
                    date_verified=verified,
                    provider=self.id,
                    retrieval=RetrievalMethod.PROVIDER_SNIPPET,
                    status=ExtractionStatus.SNIPPET_ONLY,
                )
            )
        return sources


class DDGSProvider(SearchProvider):
    """DuckDuckGo via the optional ``ddgs`` package.

    Imported lazily and degrades to "unavailable" when absent, so the package is
    genuinely optional rather than a hard dependency.
    """

    id = "ddgs"
    label = "DuckDuckGo"
    supports_web = True

    def __init__(self, *, fetcher: SafeFetcher) -> None:
        super().__init__(fetcher=fetcher, min_interval_s=1.0)

    async def _search(self, query: str, window: FreshnessWindow, limit: int) -> list[Source]:
        import asyncio

        try:
            from ddgs import DDGS  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError("ddgs is not installed") from exc

        timelimit = None
        if window.hours is not None:
            timelimit = "d" if window.hours <= 48 else "w" if window.hours <= 24 * 8 else "m"

        def run() -> list[dict[str, Any]]:
            with DDGS() as client:
                return list(client.news(query, max_results=limit, timelimit=timelimit))

        rows = await asyncio.to_thread(run)

        sources: list[Source] = []
        for row in rows:
            link = (row.get("url") or "").strip()
            title = (row.get("title") or "").strip()
            if not link or not title:
                continue
            published, verified = parse_datetime(row.get("date"))
            sources.append(
                Source(
                    url=link,
                    title=title,
                    publisher=(row.get("source") or _publisher_from_url(link)),
                    snippet=(row.get("body") or "")[:400],
                    published_at=published,
                    date_verified=verified,
                    provider=self.id,
                    retrieval=RetrievalMethod.PROVIDER_SNIPPET,
                    status=ExtractionStatus.SNIPPET_ONLY,
                )
            )
        return sources


def default_providers(fetcher: SafeFetcher, *, arabic: bool = False) -> list[SearchProvider]:
    """The provider set used for a request.

    Ordered by observed reliability. Adding a paid adapter later is a one-line
    change here.
    """
    providers: list[SearchProvider] = [
        GoogleNewsRSS(fetcher=fetcher),
        BingNewsRSS(fetcher=fetcher),
        GDELTProvider(fetcher=fetcher),
        DDGSProvider(fetcher=fetcher),
    ]
    if arabic:
        providers.insert(1, GoogleNewsArabicRSS(fetcher=fetcher))
    return providers

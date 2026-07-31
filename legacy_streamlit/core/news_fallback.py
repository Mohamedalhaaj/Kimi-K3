from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree as ET

import httpx
from ddgs import DDGS

from .web_tools import Source, fetch_public_page, is_public_web_url


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

INSTRUCTION_PATTERNS = (
    r"\bgive me\b",
    r"\bshow me\b",
    r"\bfind\b",
    r"\bsearch(?: for)?\b",
    r"\blatest\b",
    r"\bbreaking\b",
    r"\bcurrent\b",
    r"\brecent\b",
    r"\bnews\b",
    r"\bheadlines?\b",
    r"\bfrom (?:the )?last 24 hours\b",
    r"\bfrom (?:the )?past 24 hours\b",
    r"\btoday\b",
    r"\bthis week\b",
    r"\bthis month\b",
    r"\binclude only dated articles\b",
    r"\bcite every claim\b",
    r"\bcite (?:all|every) sources?\b",
    r"\bdo not include homepages\b",
    r"\bdo not include topic pages\b",
    r"\barticles? older than \d+ days?\b",
)

TAG_PATTERN = re.compile(r"<[^>]+>")
SPACE_PATTERN = re.compile(r"\s+")


def _normalize_query(query: str) -> str:
    value = query or ""
    for pattern in INSTRUCTION_PATTERNS:
        value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\b(?:please|only|dated|articles?|sources?|claims?)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = SPACE_PATTERN.sub(" ", value).strip(" .,:;-")
    return value or (query or "").strip()


def _parse_datetime(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None

    candidates = [text]
    if text.endswith("Z"):
        candidates.append(text[:-1] + "+00:00")

    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass

    for fmt in (
        "%Y%m%dT%H%M%SZ",
        "%Y%m%d%H%M%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _window_delta(timelimit: str) -> timedelta:
    return {
        "d": timedelta(hours=30),
        "w": timedelta(days=8),
        "m": timedelta(days=32),
        "y": timedelta(days=370),
    }.get(timelimit, timedelta(days=8))


def _within_window(value: str, timelimit: str) -> bool:
    parsed = _parse_datetime(value)
    if parsed is None:
        return False
    now = datetime.now(timezone.utc)
    return now - _window_delta(timelimit) <= parsed <= now + timedelta(hours=2)


def _strip_html(value: str) -> str:
    return SPACE_PATTERN.sub(
        " ",
        TAG_PATTERN.sub(" ", html.unescape(value or "")),
    ).strip()


def _source_key(source: Source) -> tuple[str, str]:
    parsed = urlparse(source.url)
    url_key = f"{parsed.netloc.lower()}{parsed.path.rstrip('/').lower()}"
    title_key = re.sub(r"\W+", "", source.title.casefold())[:140]
    return url_key, title_key


def _ddgs_news(query: str, max_results: int, timelimit: str) -> list[Source]:
    errors: list[str] = []
    raw_results: list[dict[str, Any]] = []

    for backend in ("bing", "duckduckgo", "yahoo", "auto"):
        try:
            with DDGS(timeout=12) as ddgs:
                raw_results = list(
                    ddgs.news(
                        query,
                        region="wt-wt",
                        safesearch="moderate",
                        timelimit=timelimit,
                        max_results=max_results * 3,
                        backend=backend,
                    )
                    or []
                )
            if raw_results:
                break
        except Exception as exc:
            errors.append(f"{backend}: {exc}")

    sources: list[Source] = []
    for item in raw_results:
        url = str(item.get("url") or item.get("href") or "").strip()
        published = str(item.get("date") or "").strip()
        if not url or not is_public_web_url(url) or not _within_window(published, timelimit):
            continue
        publisher = str(item.get("source") or "").strip()
        snippet = str(item.get("body") or item.get("snippet") or "").strip()
        if publisher:
            snippet = f"{publisher} — {snippet}".strip(" —")
        sources.append(
            Source(
                title=str(item.get("title") or urlparse(url).netloc).strip()[:180],
                url=url,
                snippet=snippet[:900],
                source_type="ddgs-news",
                published_at=published,
            )
        )
        if len(sources) >= max_results:
            break

    if not sources and errors:
        raise RuntimeError("; ".join(errors[-2:]))
    return sources


def _google_news_rss(query: str, max_results: int, timelimit: str) -> list[Source]:
    when = {"d": "1d", "w": "7d", "m": "30d", "y": "365d"}.get(timelimit, "7d")
    rss_query = f"{query} when:{when}"
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(rss_query)}&hl=en-US&gl=US&ceid=US:en"
    )
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml,application/xml,text/xml",
    }

    with httpx.Client(
        headers=headers,
        timeout=20,
        follow_redirects=True,
        http2=True,
    ) as client:
        response = client.get(url)
        response.raise_for_status()

    root = ET.fromstring(response.content)
    sources: list[Source] = []

    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        description = _strip_html(item.findtext("description") or "")
        source_node = item.find("source")
        publisher = (source_node.text or "").strip() if source_node is not None else ""

        if not title or not link or not is_public_web_url(link):
            continue
        if not _within_window(published, timelimit):
            continue

        snippet = description
        if publisher and publisher.casefold() not in snippet.casefold():
            snippet = f"{publisher} — {snippet}".strip(" —")

        sources.append(
            Source(
                title=title[:180],
                url=link,
                snippet=snippet[:900],
                source_type="google-news-rss",
                published_at=published,
            )
        )
        if len(sources) >= max_results:
            break

    return sources


def _gdelt_news(query: str, max_results: int, timelimit: str) -> list[Source]:
    timespan = {"d": "24h", "w": "7d", "m": "30d", "y": "1y"}.get(timelimit, "7d")
    endpoint = "https://api.gdeltproject.org/api/v2/doc/doc"
    params = {
        "query": query,
        "mode": "ArtList",
        "maxrecords": min(max_results * 5, 75),
        "timespan": timespan,
        "sort": "DateDesc",
        "format": "json",
    }
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    with httpx.Client(
        headers=headers,
        timeout=25,
        follow_redirects=True,
        http2=True,
    ) as client:
        response = client.get(endpoint, params=params)
        response.raise_for_status()
        payload = response.json()

    articles = payload.get("articles") if isinstance(payload, dict) else None
    if not isinstance(articles, list):
        return []

    sources: list[Source] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        url = str(article.get("url") or "").strip()
        title = str(article.get("title") or "").strip()
        published = str(
            article.get("seendate")
            or article.get("date")
            or article.get("datetime")
            or ""
        ).strip()
        if not url or not title or not is_public_web_url(url):
            continue
        if not _within_window(published, timelimit):
            continue

        domain = str(article.get("domain") or urlparse(url).netloc).strip()
        language = str(article.get("language") or "").strip()
        source_country = str(article.get("sourcecountry") or "").strip()
        metadata = " · ".join(part for part in (domain, language, source_country) if part)

        sources.append(
            Source(
                title=title[:180],
                url=url,
                snippet=metadata[:900],
                source_type="gdelt",
                published_at=published,
            )
        )
        if len(sources) >= max_results:
            break

    return sources


def _read_top_articles(sources: list[Source], limit: int = 3) -> None:
    read_count = 0
    for source in sources:
        if read_count >= limit:
            break
        try:
            title, content, final_url = fetch_public_page(source.url)
            if content:
                source.title = title or source.title
                source.content = content
                source.url = final_url
                read_count += 1
        except Exception:
            continue


def search_news_robust(
    query: str,
    max_results: int = 6,
    timelimit: str = "w",
) -> list[Source]:
    """Aggregate recent news from independent providers with strict date filtering."""
    clean_query = _normalize_query(query)
    provider_errors: list[str] = []
    collected: list[Source] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    providers = (
        ("DDGS News", _ddgs_news),
        ("Google News RSS", _google_news_rss),
        ("GDELT", _gdelt_news),
    )

    for provider_name, provider in providers:
        try:
            provider_sources = provider(
                clean_query,
                max_results=max_results,
                timelimit=timelimit,
            )
        except Exception as exc:
            provider_errors.append(f"{provider_name}: {exc}")
            continue

        for source in provider_sources:
            url_key, title_key = _source_key(source)
            if not url_key or url_key in seen_urls or (title_key and title_key in seen_titles):
                continue
            seen_urls.add(url_key)
            if title_key:
                seen_titles.add(title_key)
            collected.append(source)

        if len(collected) >= max_results:
            break

    collected.sort(
        key=lambda source: _parse_datetime(source.published_at)
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    collected = collected[:max_results]

    if not collected:
        details = "; ".join(provider_errors) or "all providers returned zero dated results"
        raise RuntimeError(f"Recent-news providers failed: {details}")

    _read_top_articles(collected, limit=min(3, len(collected)))
    return collected

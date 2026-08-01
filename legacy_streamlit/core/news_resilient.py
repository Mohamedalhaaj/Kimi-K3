from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree as ET

import httpx

from .news_fallback import (
    USER_AGENT,
    _ddgs_news,
    _gdelt_news,
    _parse_datetime,
    _read_top_articles,
    _source_key,
    _within_window,
)
from .web_tools import Source, is_public_web_url


SENTENCE_SPLIT = re.compile(r"[\n\r.!?؟؛]+")
SPACE_PATTERN = re.compile(r"\s+")
TAG_PATTERN = re.compile(r"<[^>]+>")

ENGLISH_NOISE = (
    r"\bgive me\b",
    r"\bshow me\b",
    r"\bfind(?: me)?\b",
    r"\bsearch(?: for)?\b",
    r"\blook up\b",
    r"\bthe latest\b",
    r"\blatest\b",
    r"\bbreaking\b",
    r"\bcurrent\b",
    r"\brecent\b",
    r"\bnews\b",
    r"\bheadlines?\b",
    r"\bfrom (?:the )?(?:last|past) 24 hours\b",
    r"\bfrom today\b",
    r"\btoday\b",
    r"\bthis week\b",
    r"\bthis month\b",
    r"\bplease\b",
    r"\binclude\b.*$",
    r"\bcite\b.*$",
    r"\bprovide\b.*$",
    r"\bonly\b",
    r"\bdated\b",
    r"\barticles?\b",
    r"\bpublication dates?\b",
    r"\bsources?\b",
    r"\bclaims?\b",
    r"\bitems?\b",
)

ARABIC_NOISE = (
    r"أعطني",
    r"اعطني",
    r"أظهر لي",
    r"اظهر لي",
    r"ابحث(?: لي)?",
    r"أحدث",
    r"احدث",
    r"آخر",
    r"اخر",
    r"الأخبار",
    r"الاخبار",
    r"أخبار",
    r"اخبار",
    r"العاجلة",
    r"عاجل",
    r"خلال آخر 24 ساعة",
    r"خلال اخر 24 ساعة",
    r"اليوم",
    r"هذا الأسبوع",
    r"هذا الاسبوع",
    r"هذا الشهر",
    r"يرجى",
    r"من فضلك",
    r"اذكر المصادر.*$",
    r"استشهد.*$",
    r"أدرج.*$",
    r"ادرج.*$",
)


def _strip_html(value: str) -> str:
    return SPACE_PATTERN.sub(
        " ",
        TAG_PATTERN.sub(" ", html.unescape(value or "")),
    ).strip()


def simplify_news_query(query: str) -> str:
    """Reduce a conversational request to the actual news topic."""
    value = (query or "").strip()
    if not value:
        return value

    # The first sentence normally contains the topic; later sentences are often
    # formatting instructions such as citation/date requirements.
    first_sentence = next(
        (part.strip() for part in SENTENCE_SPLIT.split(value) if part.strip()),
        value,
    )

    cleaned = first_sentence
    for pattern in (*ENGLISH_NOISE, *ARABIC_NOISE):
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(
        r"\b(?:with|and|every|all|of|for|about|on|regarding)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = SPACE_PATTERN.sub(" ", cleaned).strip(" .,:;-—")

    # Fall back to the original first sentence only if cleaning removed
    # everything. In normal requests this turns the example prompt into Libya.
    return cleaned or first_sentence


def _rss_sources(
    url: str,
    max_results: int,
    timelimit: str,
    source_type: str,
) -> list[Source]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml,application/xml,text/xml,*/*;q=0.7",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.7",
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

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = (
            item.findtext("pubDate")
            or item.findtext("date")
            or ""
        ).strip()
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
                source_type=source_type,
                published_at=published,
            )
        )
        if len(sources) >= max_results:
            break

    return sources


def _google_news_rss_multi(
    query: str,
    max_results: int,
    timelimit: str,
) -> list[Source]:
    when = {"d": "1d", "w": "7d", "m": "30d", "y": "365d"}.get(
        timelimit,
        "7d",
    )
    variants = [query]
    if len(query.split()) > 1:
        variants.append(f'"{query}"')

    combined: list[Source] = []
    seen: set[str] = set()
    for variant in variants:
        rss_query = f"{variant} when:{when}"
        url = (
            "https://news.google.com/rss/search?"
            f"q={quote_plus(rss_query)}&hl=en-US&gl=US&ceid=US:en"
        )
        for source in _rss_sources(
            url,
            max_results=max_results,
            timelimit=timelimit,
            source_type="google-news-rss",
        ):
            key = source.url
            if key in seen:
                continue
            seen.add(key)
            combined.append(source)
            if len(combined) >= max_results:
                return combined
    return combined


def _bing_news_rss(
    query: str,
    max_results: int,
    timelimit: str,
) -> list[Source]:
    # Bing's RSS endpoint does not expose a documented freshness parameter, so
    # strict date filtering is applied locally with _within_window().
    url = (
        "https://www.bing.com/news/search?"
        f"q={quote_plus(query)}&format=rss&setlang=en-US"
    )
    return _rss_sources(
        url,
        max_results=max_results,
        timelimit=timelimit,
        source_type="bing-news-rss",
    )


def search_news_resilient(
    query: str,
    max_results: int = 6,
    timelimit: str = "w",
) -> list[Source]:
    """Search recent news through independent providers without API keys."""
    topic = simplify_news_query(query)
    providers = (
        ("Google News RSS", _google_news_rss_multi),
        ("Bing News RSS", _bing_news_rss),
        ("DDGS News", _ddgs_news),
        ("GDELT", _gdelt_news),
    )

    collected: list[Source] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    errors: list[str] = []

    for provider_name, provider in providers:
        try:
            provider_sources = provider(
                topic,
                max_results=max_results,
                timelimit=timelimit,
            )
        except Exception as exc:
            errors.append(f"{provider_name}: {exc}")
            continue

        for source in provider_sources:
            url_key, title_key = _source_key(source)
            if not url_key or url_key in seen_urls:
                continue
            if title_key and title_key in seen_titles:
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
        details = "; ".join(errors) or (
            f"all providers returned zero dated results for topic: {topic}"
        )
        raise RuntimeError(f"Recent-news providers failed: {details}")

    _read_top_articles(collected, limit=min(3, len(collected)))
    return collected

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS

from .web_tools import Source, fetch_public_page, is_public_web_url


AGGREGATOR_HOSTS = {
    "news.google.com",
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
    "news.yahoo.com",
    "yahoo.com",
    "www.yahoo.com",
}
REDIRECT_QUERY_KEYS = (
    "url",
    "u",
    "r",
    "target",
    "dest",
    "destination",
    "redirect",
    "redirect_url",
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
TRACKING_HOST_PARTS = (
    "doubleclick",
    "googlesyndication",
    "googleadservices",
    "analytics",
    "facebook.com/tr",
)


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _is_aggregator(url: str) -> bool:
    host = _hostname(url)
    return host in {value.removeprefix("www.") for value in AGGREGATOR_HOSTS}


def _is_candidate_article(url: str) -> bool:
    if not url or not is_public_web_url(url) or _is_aggregator(url):
        return False
    lowered = url.casefold()
    if any(part in lowered for part in TRACKING_HOST_PARTS):
        return False
    return True


def _decode_repeatedly(value: str, rounds: int = 3) -> str:
    decoded = value
    for _ in range(rounds):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def _query_redirect_candidate(url: str) -> str | None:
    params = parse_qs(urlparse(url).query)
    for key in REDIRECT_QUERY_KEYS:
        for value in params.get(key, []):
            candidate = _decode_repeatedly(value).strip()
            if candidate.startswith("//"):
                candidate = "https:" + candidate
            if _is_candidate_article(candidate):
                return candidate
    return None


def _metadata_candidates(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []

    selectors = (
        ("link", {"rel": "canonical"}, "href"),
        ("link", {"rel": "alternate"}, "href"),
        ("meta", {"property": "og:url"}, "content"),
        ("meta", {"name": "twitter:url"}, "content"),
    )
    for tag_name, attrs, attribute in selectors:
        for tag in soup.find_all(tag_name, attrs=attrs):
            value = str(tag.get(attribute) or "").strip()
            if value:
                candidates.append(urljoin(base_url, value))

    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, str(anchor.get("href") or "").strip())
        if href:
            candidates.append(href)

    unique: list[str] = []
    for candidate in candidates:
        if _is_candidate_article(candidate) and candidate not in unique:
            unique.append(candidate)
    return unique


def _resolve_from_aggregator_page(url: str) -> str | None:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.7",
    }
    with httpx.Client(
        headers=headers,
        timeout=18,
        follow_redirects=True,
        http2=True,
    ) as client:
        response = client.get(url)
        response.raise_for_status()

    final_url = str(response.url)
    if _is_candidate_article(final_url):
        return final_url

    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type:
        return None

    for candidate in _metadata_candidates(response.text[:2_500_000], final_url):
        return candidate
    return None


def _clean_headline_for_search(title: str) -> str:
    value = re.sub(r"\s+[—–-]\s+[^—–-]{2,80}$", "", title or "").strip()
    return value or (title or "").strip()


def _search_exact_headline(title: str, max_results: int = 8) -> str | None:
    clean_title = _clean_headline_for_search(title)
    if not clean_title:
        return None

    query = f'"{clean_title}"'
    try:
        with DDGS(timeout=12) as ddgs:
            results = list(
                ddgs.text(
                    query,
                    region="wt-wt",
                    safesearch="moderate",
                    max_results=max_results,
                    backend="auto",
                )
                or []
            )
    except Exception:
        return None

    normalized_title = re.sub(r"\W+", "", clean_title.casefold())
    ranked: list[tuple[int, str]] = []
    for item in results:
        url = str(item.get("href") or item.get("url") or "").strip()
        if not _is_candidate_article(url):
            continue
        result_title = str(item.get("title") or "")
        normalized_result = re.sub(r"\W+", "", result_title.casefold())
        score = 0
        if normalized_title and normalized_title in normalized_result:
            score += 4
        shared_words = set(clean_title.casefold().split()) & set(result_title.casefold().split())
        score += min(len(shared_words), 5)
        ranked.append((score, url))

    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][1]


def resolve_article_url(source: Source) -> str | None:
    """Resolve RSS/search aggregator links into a publisher article URL."""
    if _is_candidate_article(source.url):
        return source.url

    query_candidate = _query_redirect_candidate(source.url)
    if query_candidate:
        return query_candidate

    try:
        page_candidate = _resolve_from_aggregator_page(source.url)
        if page_candidate:
            return page_candidate
    except Exception:
        pass

    return _search_exact_headline(source.title)


def enrich_source_with_full_text(source: Source) -> bool:
    """Resolve and read one source. Returns True only when article text was read."""
    candidates: list[str] = []
    resolved = resolve_article_url(source)
    if resolved:
        candidates.append(resolved)
    if source.url not in candidates and _is_candidate_article(source.url):
        candidates.append(source.url)

    for candidate in candidates:
        try:
            title, content, final_url = fetch_public_page(candidate)
        except Exception:
            continue
        if not content or len(content.strip()) < 250:
            continue
        source.url = final_url
        source.title = title or source.title
        source.content = content
        return True
    return False


def enrich_news_sources(sources: list[Source], limit: int = 5) -> tuple[int, int]:
    """Resolve/read up to ``limit`` sources and return (full_text, snippet_only)."""
    full_text = 0
    attempted = 0
    for source in sources:
        if attempted >= limit:
            break
        attempted += 1
        if enrich_source_with_full_text(source):
            full_text += 1
    return full_text, max(0, len(sources) - full_text)

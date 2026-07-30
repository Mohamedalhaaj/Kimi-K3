from __future__ import annotations

import io
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup
from ddgs import DDGS
from pypdf import PdfReader


URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
AUTO_BROWSE_TERMS = (
    "search",
    "browse",
    "look up",
    "find online",
    "open this",
    "open the link",
    "latest",
    "today",
    "current",
    "recent",
    "news",
    "verify",
    "source",
    "research",
    "website",
    "web",
    "ابحث",
    "تصفح",
    "افتح",
    "الرابط",
    "آخر",
    "اخر",
    "أحدث",
    "اليوم",
    "حالي",
    "أخبار",
    "اخبار",
    "تحقق",
    "مصدر",
)

MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_PAGE_CHARS = 18_000
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"
)


@dataclass(slots=True)
class Source:
    title: str
    url: str
    snippet: str = ""
    content: str = ""
    source_type: str = "search"


@dataclass(slots=True)
class ToolResult:
    context: str = ""
    sources: list[Source] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def extract_urls(text: str, limit: int = 5) -> list[str]:
    urls: list[str] = []
    for match in URL_PATTERN.findall(text or ""):
        cleaned = match.rstrip(".,;:!?)]}'\"")
        if cleaned and cleaned not in urls:
            urls.append(cleaned)
        if len(urls) >= limit:
            break
    return urls


def _resolve_public_addresses(hostname: str) -> list[str]:
    addresses: list[str] = []
    for entry in socket.getaddrinfo(hostname, None):
        ip_text = entry[4][0]
        if ip_text not in addresses:
            addresses.append(ip_text)
    return addresses


def is_public_web_url(url: str) -> bool:
    """Reject localhost/private-network URLs to reduce SSRF risk."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False

        hostname = parsed.hostname.lower().strip(".")
        if hostname in {"localhost", "localhost.localdomain"}:
            return False

        for ip_text in _resolve_public_addresses(hostname):
            ip = ipaddress.ip_address(ip_text)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                return False
        return True
    except (OSError, ValueError):
        return False


def should_browse(text: str, mode: str) -> bool:
    if mode == "Off":
        return False
    if mode == "Always":
        return True
    if extract_urls(text):
        return True
    lowered = (text or "").casefold()
    return any(term in lowered for term in AUTO_BROWSE_TERMS)


def _decode_text(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    raw = response.content[:MAX_PAGE_BYTES]

    if "application/pdf" in content_type or response.url.path.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(raw))
        parts: list[str] = []
        for page in reader.pages[:80]:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text.strip())
            if sum(len(part) for part in parts) >= MAX_PAGE_CHARS:
                break
        return "\n\n".join(parts)[:MAX_PAGE_CHARS]

    text = response.text
    if "text/html" in content_type or "<html" in text[:1000].lower():
        extracted = trafilatura.extract(
            text,
            include_links=False,
            include_images=False,
            include_tables=True,
            output_format="txt",
            favor_precision=True,
        )
        if extracted and extracted.strip():
            return extracted.strip()[:MAX_PAGE_CHARS]

        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        return "\n".join(soup.stripped_strings)[:MAX_PAGE_CHARS]

    return text.strip()[:MAX_PAGE_CHARS]


@lru_cache(maxsize=128)
def fetch_public_page(url: str, timeout_seconds: int = 15) -> tuple[str, str, str]:
    """Return title, readable text, and final URL for a public page."""
    if not is_public_web_url(url):
        raise ValueError("The URL is not a permitted public web address.")

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.7",
    }
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)

    with httpx.Client(
        headers=headers,
        timeout=timeout_seconds,
        follow_redirects=True,
        limits=limits,
    ) as client:
        response = client.get(url)
        response.raise_for_status()

        final_url = str(response.url)
        if not is_public_web_url(final_url):
            raise ValueError("The page redirected to a non-public address.")

        text = _decode_text(response)
        if not text:
            raise ValueError("The page returned no readable text.")

        title = urlparse(final_url).netloc
        content_type = response.headers.get("content-type", "").lower()
        if "html" in content_type:
            soup = BeautifulSoup(response.text[:2_000_000], "html.parser")
            if soup.title and soup.title.string:
                title = soup.title.string.strip()[:180]

        return title, text, final_url


def _clean_search_query(text: str) -> str:
    query = URL_PATTERN.sub(" ", text or "")
    query = re.sub(r"\s+", " ", query).strip()
    for prefix in ("/search", "/browse", "search:", "ابحث:"):
        if query.casefold().startswith(prefix.casefold()):
            query = query[len(prefix):].strip()
    return query


def _safe_title(value: Any, fallback: str) -> str:
    title = str(value or "").strip()
    return title[:180] if title else fallback


def search_web(query: str, max_results: int = 5) -> list[Source]:
    query = query.strip()
    if not query:
        return []

    results: list[Source] = []
    with DDGS(timeout=12) as ddgs:
        raw_results = ddgs.text(
            query,
            region="wt-wt",
            safesearch="moderate",
            max_results=max_results,
            backend="auto",
        )
        for item in raw_results or []:
            url = str(item.get("href") or item.get("url") or "").strip()
            if not url or not is_public_web_url(url):
                continue
            results.append(
                Source(
                    title=_safe_title(item.get("title"), urlparse(url).netloc),
                    url=url,
                    snippet=str(item.get("body") or item.get("snippet") or "").strip()[:900],
                    source_type="search",
                )
            )
    return results


def browse_web(
    query: str,
    mode: str,
    depth: str,
    max_results: int = 5,
    max_context_chars: int = 30_000,
) -> ToolResult:
    result = ToolResult()
    if not should_browse(query, mode):
        return result

    urls = extract_urls(query)
    seen_urls: set[str] = set()

    for url in urls:
        try:
            title, content, final_url = fetch_public_page(url)
            if final_url in seen_urls:
                continue
            seen_urls.add(final_url)
            result.sources.append(
                Source(
                    title=title,
                    url=final_url,
                    snippet="Direct link supplied by the user.",
                    content=content,
                    source_type="direct",
                )
            )
            result.events.append(f"Opened {title}")
        except Exception as exc:
            result.warnings.append(f"Could not open {url}: {exc}")

    clean_query = _clean_search_query(query)
    lowered = query.casefold()
    explicit_search = any(term in lowered for term in AUTO_BROWSE_TERMS)
    run_search = not urls or mode == "Always" or (explicit_search and len(clean_query) >= 3)

    if run_search:
        try:
            search_query = clean_query or query
            search_sources = search_web(search_query, max_results=max_results)
            result.events.append(f"Searched the web for: {search_query}")

            for index, source in enumerate(search_sources):
                if source.url in seen_urls:
                    continue
                seen_urls.add(source.url)

                if depth == "Deep" and index < min(3, max_results):
                    try:
                        title, content, final_url = fetch_public_page(source.url)
                        source.title = title or source.title
                        source.url = final_url
                        source.content = content
                        result.events.append(f"Read {source.title}")
                    except Exception as exc:
                        result.warnings.append(f"Could not read {source.url}: {exc}")

                result.sources.append(source)
        except Exception as exc:
            result.warnings.append(f"Web search failed: {exc}")

    sections: list[str] = []
    for index, source in enumerate(result.sources, start=1):
        section = [
            f"[{index}] {source.title}",
            f"URL: {source.url}",
        ]
        if source.snippet:
            section.append(f"SEARCH SUMMARY:\n{source.snippet}")
        if source.content:
            section.append(f"PAGE CONTENT:\n{source.content}")
        sections.append("\n".join(section))

    context = "\n\n---\n\n".join(sections)[:max_context_chars]
    if context:
        result.context = (
            "WEB TOOL RESULTS\n"
            "Use these numbered sources for current or web-grounded claims. "
            "Cite them inline as [1], [2], etc. Do not claim that you cannot browse "
            "when these results are present. If the sources are insufficient, say so.\n\n"
            f"{context}"
        )
    return result

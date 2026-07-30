from __future__ import annotations

import io
import ipaddress
import os
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


HTTP_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
BARE_DOMAIN_PATTERN = re.compile(
    r"(?<![@\w])((?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}"
    r"(?::\d{2,5})?(?:/[^\s<>\"']*)?)",
    re.IGNORECASE,
)
AUTO_BROWSE_TERMS = (
    "search",
    "browse",
    "look up",
    "find online",
    "find on",
    "open this",
    "open the link",
    "open ",
    "visit",
    "website",
    "official site",
    "latest",
    "today",
    "current",
    "recent",
    "news",
    "verify",
    "source",
    "research",
    "web",
    "price",
    "prices",
    "ابحث",
    "تصفح",
    "افتح",
    "زر",
    "الموقع",
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
    "سعر",
    "الأسعار",
)
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_PAGE_CHARS = 20_000
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
BLOCK_PAGE_MARKERS = (
    "enable javascript",
    "access denied",
    "verify you are human",
    "checking your browser",
    "captcha",
    "unusual traffic",
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


def _clean_url_candidate(value: str) -> str:
    return value.rstrip(".,;:!?)]}'\"")


def extract_urls(text: str, limit: int = 5) -> list[str]:
    """Extract full URLs and bare domains such as apple.com."""
    value = text or ""
    urls: list[str] = []

    for match in HTTP_URL_PATTERN.findall(value):
        cleaned = _clean_url_candidate(match)
        if cleaned and cleaned not in urls:
            urls.append(cleaned)
        if len(urls) >= limit:
            return urls

    value_without_http = HTTP_URL_PATTERN.sub(" ", value)
    for match in BARE_DOMAIN_PATTERN.findall(value_without_http):
        cleaned = _clean_url_candidate(match)
        if not cleaned:
            continue
        normalized = f"https://{cleaned}"
        if normalized not in urls:
            urls.append(normalized)
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

        addresses = _resolve_public_addresses(hostname)
        if not addresses:
            return False

        for ip_text in addresses:
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


def _read_limited_response(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        if not chunk:
            continue
        remaining = MAX_PAGE_BYTES - total
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        total += min(len(chunk), remaining)
        if total >= MAX_PAGE_BYTES:
            break
    return b"".join(chunks)


def _decode_content(raw: bytes, content_type: str, final_url: str) -> str:
    content_type = (content_type or "").lower()

    if "application/pdf" in content_type or urlparse(final_url).path.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(raw))
        parts: list[str] = []
        total = 0
        for page in reader.pages[:80]:
            text = (page.extract_text() or "").strip()
            if text:
                parts.append(text)
                total += len(text)
            if total >= MAX_PAGE_CHARS:
                break
        return "\n\n".join(parts)[:MAX_PAGE_CHARS]

    text = raw.decode("utf-8", errors="replace")
    if "text/html" in content_type or "<html" in text[:1200].lower():
        extracted = trafilatura.extract(
            text,
            include_links=True,
            include_images=False,
            include_tables=True,
            output_format="txt",
            favor_precision=False,
            favor_recall=True,
        )
        if extracted and extracted.strip():
            return extracted.strip()[:MAX_PAGE_CHARS]

        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        return "\n".join(soup.stripped_strings)[:MAX_PAGE_CHARS]

    return text.strip()[:MAX_PAGE_CHARS]


def _looks_blocked_or_empty(text: str) -> bool:
    stripped = (text or "").strip()
    if len(stripped) < 250:
        return True
    lowered = stripped.casefold()
    return any(marker in lowered for marker in BLOCK_PAGE_MARKERS)


def _fetch_direct(url: str, timeout_seconds: int) -> tuple[str, str, str]:
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)

    with httpx.Client(
        headers=headers,
        timeout=timeout_seconds,
        follow_redirects=True,
        limits=limits,
        http2=True,
    ) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            final_url = str(response.url)
            if not is_public_web_url(final_url):
                raise ValueError("The page redirected to a non-public address.")
            raw = _read_limited_response(response)
            content_type = response.headers.get("content-type", "")
            text = _decode_content(raw, content_type, final_url)

        title = urlparse(final_url).netloc
        if "html" in content_type.lower():
            html_text = raw.decode("utf-8", errors="replace")
            soup = BeautifulSoup(html_text[:2_000_000], "html.parser")
            if soup.title and soup.title.string:
                title = soup.title.string.strip()[:180]

        return title, text, final_url


def _fetch_via_jina(url: str, timeout_seconds: int) -> tuple[str, str, str]:
    """Use Jina Reader as a fallback for JavaScript-heavy or blocked pages."""
    reader_url = f"https://r.jina.ai/{url}"
    headers = {
        "Accept": "text/plain",
        "User-Agent": DEFAULT_USER_AGENT,
        "X-Return-Format": "text",
    }
    jina_key = os.getenv("JINA_API_KEY")
    if jina_key:
        headers["Authorization"] = f"Bearer {jina_key}"

    with httpx.Client(
        headers=headers,
        timeout=max(timeout_seconds, 25),
        follow_redirects=True,
        http2=True,
    ) as client:
        response = client.get(reader_url)
        response.raise_for_status()
        text = response.text.strip()[:MAX_PAGE_CHARS]

    if not text:
        raise ValueError("The fallback reader returned no readable text.")

    title = urlparse(url).netloc
    title_match = re.search(r"^Title:\s*(.+)$", text, re.MULTILINE)
    if title_match:
        title = title_match.group(1).strip()[:180]

    return title, text, url


@lru_cache(maxsize=128)
def fetch_public_page(url: str, timeout_seconds: int = 15) -> tuple[str, str, str]:
    """Read a public page, using a browser-rendering fallback when necessary."""
    if not is_public_web_url(url):
        raise ValueError("The URL is not a permitted public web address.")

    direct_error: Exception | None = None
    try:
        title, text, final_url = _fetch_direct(url, timeout_seconds)
        if not _looks_blocked_or_empty(text):
            return title, text, final_url
    except Exception as exc:
        direct_error = exc

    try:
        return _fetch_via_jina(url, timeout_seconds)
    except Exception as fallback_error:
        if direct_error:
            raise ValueError(
                f"Direct reader failed ({direct_error}); fallback reader failed "
                f"({fallback_error})."
            ) from fallback_error
        raise ValueError(f"Fallback reader failed: {fallback_error}") from fallback_error


def _remove_urls_and_domains(text: str) -> str:
    query = HTTP_URL_PATTERN.sub(" ", text or "")
    query = BARE_DOMAIN_PATTERN.sub(" ", query)
    return re.sub(r"\s+", " ", query).strip()


def _clean_search_query(text: str) -> str:
    query = _remove_urls_and_domains(text)
    for prefix in (
        "/search",
        "/browse",
        "search:",
        "browse:",
        "ابحث:",
        "افتح:",
    ):
        if query.casefold().startswith(prefix.casefold()):
            query = query[len(prefix):].strip()
    return query


def _safe_title(value: Any, fallback: str) -> str:
    title = str(value or "").strip()
    return title[:180] if title else fallback


def _search_once(query: str, backend: str, max_results: int) -> list[dict[str, Any]]:
    with DDGS(timeout=12) as ddgs:
        return list(
            ddgs.text(
                query,
                region="wt-wt",
                safesearch="moderate",
                max_results=max_results,
                backend=backend,
            )
            or []
        )


def search_web(
    query: str,
    max_results: int = 5,
    preferred_domains: list[str] | None = None,
) -> list[Source]:
    query = query.strip()
    if not query:
        return []

    preferred_domains = preferred_domains or []
    if preferred_domains and not any(f"site:{domain}" in query for domain in preferred_domains):
        site_filter = " OR ".join(f"site:{domain}" for domain in preferred_domains[:3])
        query = f"({site_filter}) {query}".strip()

    raw_results: list[dict[str, Any]] = []
    errors: list[str] = []
    for backend in ("bing", "duckduckgo", "brave", "google", "auto"):
        try:
            raw_results = _search_once(query, backend, max_results=max_results * 2)
            if raw_results:
                break
        except Exception as exc:
            errors.append(f"{backend}: {exc}")

    if not raw_results and errors:
        raise RuntimeError("; ".join(errors[-2:]))

    results: list[Source] = []
    seen: set[str] = set()
    for item in raw_results:
        url = str(item.get("href") or item.get("url") or "").strip()
        if not url or url in seen or not is_public_web_url(url):
            continue
        seen.add(url)
        results.append(
            Source(
                title=_safe_title(item.get("title"), urlparse(url).netloc),
                url=url,
                snippet=str(item.get("body") or item.get("snippet") or "").strip()[:900],
                source_type="search",
            )
        )
        if len(results) >= max_results:
            break

    if preferred_domains:
        preferred_set = tuple(domain.lower() for domain in preferred_domains)
        results.sort(
            key=lambda source: (
                0
                if (urlparse(source.url).hostname or "").lower().removeprefix("www.")
                in preferred_set
                else 1
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
    preferred_domains: list[str] = []
    seen_urls: set[str] = set()

    for url in urls:
        hostname = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if hostname and hostname not in preferred_domains:
            preferred_domains.append(hostname)
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
            result.events.append(f"Opened and read {title}")
        except Exception as exc:
            result.warnings.append(f"Could not open {url}: {exc}")

    clean_query = _clean_search_query(query)
    lowered = (query or "").casefold()
    explicit_search = any(term in lowered for term in AUTO_BROWSE_TERMS)
    run_search = not urls or mode == "Always" or (explicit_search and len(clean_query) >= 3)

    if run_search:
        try:
            search_query = clean_query or query
            search_sources = search_web(
                search_query,
                max_results=max_results,
                preferred_domains=preferred_domains,
            )
            result.events.append(f"Searched the web for: {search_query}")

            for index, source in enumerate(search_sources):
                if source.url in seen_urls:
                    continue
                seen_urls.add(source.url)

                should_read = depth == "Deep" or (
                    preferred_domains
                    and (urlparse(source.url).hostname or "")
                    .lower()
                    .removeprefix("www.")
                    in preferred_domains
                )
                if should_read and index < min(4, max_results):
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
        section = [f"[{index}] {source.title}", f"URL: {source.url}"]
        if source.snippet:
            section.append(f"SEARCH SUMMARY:\n{source.snippet}")
        if source.content:
            section.append(f"PAGE CONTENT:\n{source.content}")
        sections.append("\n".join(section))

    context = "\n\n---\n\n".join(sections)[:max_context_chars]
    if context:
        result.context = (
            "WEB TOOL RESULTS\n"
            "The application successfully used its web tool. Use these numbered "
            "sources for current or web-grounded claims and cite them inline as "
            "[1], [2], etc. Do not say that you cannot browse, cannot see the site, "
            "or are relying only on training knowledge when readable sources are "
            "present. If a source is insufficient, identify the exact limitation.\n\n"
            f"{context}"
        )
    elif result.warnings:
        result.context = (
            "WEB TOOL STATUS\n"
            "The application attempted to browse, but no readable source content was "
            "retrieved. Do not invent current facts. Briefly state that the web tool "
            "failed for this request and use the supplied warnings to explain why.\n\n"
            + "\n".join(f"- {warning}" for warning in result.warnings)
        )

    return result

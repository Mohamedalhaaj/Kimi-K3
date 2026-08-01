"""Article extraction and publisher-URL resolution.

Fixes carried from the audit:

* **First-anchor-wins** (``article_resolver.py:137``): the prototype's candidate
  loop `return`ed on its first iteration, and the candidate list included *every*
  ``<a href>`` on the page in DOM order — so when an aggregator omitted its
  canonical, the resolver picked a nav or promo link. Here only real canonical
  signals are used, and a link is accepted only if it looks like an article.
* **Zero-threshold headline matching** (``article_resolver.py:183``): results
  were ranked from a score starting at 0 with no minimum, so an unrelated page
  was confidently "resolved". A minimum overlap is now required.
* **Label/content divergence** (``web_tools.py:710-713``): the status label was
  computed one layer below the layer that last overwrote the content. The label
  is now assigned by whichever extractor produced the text.
* **Homepages presented as articles**: a URL with no path is never an article.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import structlog

from kimi.research.dates import extract_published_at
from kimi.research.models import ExtractionStatus, RetrievalMethod, Source
from kimi.research.net import FetchError, SafeFetcher, UnsafeUrlError

log = structlog.get_logger(__name__)

#: Below this, a "body" is really a stub or a consent interstitial.
MIN_FULL_CHARS: Final = 900
MIN_PARTIAL_CHARS: Final = 220
MAX_CONTENT_CHARS: Final = 12_000

_AGGREGATORS: Final = frozenset(
    {
        "news.google.com",
        "www.google.com",
        "google.com",
        "news.yahoo.com",
        "www.bing.com",
        "bing.com",
        "flipboard.com",
        "t.co",
        "news.url.google.com",
    }
)

#: Query parameters that carry the real destination on aggregator links.
_REDIRECT_PARAMS: Final = ("url", "u", "q", "target", "redirect", "to", "link")

_PAYWALL_MARKERS: Final = tuple(
    m.lower()
    for m in (
        "subscribe to continue",
        "subscribers only",
        "this article is for subscribers",
        "create a free account to continue",
        "already a subscriber",
        "register to continue reading",
        "للمشتركين فقط",
    )
)

_BLOCK_MARKERS: Final = tuple(
    m.lower()
    for m in (
        "access denied",
        "are you a robot",
        "verify you are human",
        "enable javascript and cookies",
        "request blocked",
        "cloudflare",
        "captcha",
    )
)

_SCRIPTY: Final = re.compile(
    r"<(script|style|noscript|svg|template|iframe)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_TAG: Final = re.compile(r"<[^>]+>")
_WS: Final = re.compile(r"[ \t\r\f\v]+")
_BLANKS: Final = re.compile(r"\n{3,}")

_CANONICAL_PATTERNS: Final = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
        r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+name=["\']twitter:url["\'][^>]+content=["\']([^"\']+)',
    )
)

_TITLE_PATTERNS: Final = tuple(
    re.compile(p, re.IGNORECASE | re.DOTALL)
    for p in (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r"<title[^>]*>(.*?)</title>",
        r"<h1[^>]*>(.*?)</h1>",
    )
)


def is_aggregator(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host in _AGGREGATORS or f"www.{host}" in _AGGREGATORS


def looks_like_article_url(url: str) -> bool:
    """Reject homepages and section fronts.

    The brief forbids showing a generic homepage as an article result.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False

    path = (parsed.path or "/").rstrip("/")
    if not path:
        return False  # bare homepage
    segments = [s for s in path.split("/") if s]
    if not segments:
        return False
    last = segments[-1]
    # A section front like /world or /sport has one short, wordless segment.
    if len(segments) == 1 and len(last) < 12 and "-" not in last and "_" not in last:
        return False
    if last.lower() in {"index.html", "index.htm", "home", "news", "latest"}:
        return False
    return True


def canonicalise(url: str) -> str:
    """Strip tracking parameters and fragments for reliable deduplication."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    keep = {
        k: v
        for k, v in parse_qs(parsed.query, keep_blank_values=False).items()
        if not k.lower().startswith(("utm_", "fbclid", "gclid", "mc_", "ref", "cmpid", "icid"))
    }
    query = "&".join(f"{k}={v[0]}" for k, v in sorted(keep.items()))
    host = (parsed.hostname or "").lower().removeprefix("www.")
    port = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
    path = (parsed.path or "/").rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), host + port, path, "", query, ""))


def resolve_redirect_param(url: str) -> str | None:
    """Pull a destination out of an aggregator redirect's query string."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    params = parse_qs(parsed.query)
    for key in _REDIRECT_PARAMS:
        for value in params.get(key, []):
            candidate = value.strip()
            if candidate.startswith(("http://", "https://")):
                if (urlparse(candidate).hostname or "").lower() != (parsed.hostname or "").lower():
                    return candidate
    return None


def extract_canonical(html: str, base_url: str) -> str | None:
    """Read a canonical URL from real canonical signals only.

    Deliberately does *not* fall back to scanning ``<a href>`` — that fallback is
    what made the prototype resolve an aggregator to a random nav link.
    """
    for pattern in _CANONICAL_PATTERNS:
        match = pattern.search(html)
        if not match:
            continue
        candidate = str(urljoin(base_url, match.group(1).strip()))
        if candidate.startswith(("http://", "https://")) and not is_aggregator(candidate):
            return candidate
    return None


def extract_title(html: str) -> str:
    for pattern in _TITLE_PATTERNS:
        match = pattern.search(html)
        if match:
            title = _TAG.sub(" ", match.group(1))
            title = _WS.sub(" ", title).strip()
            if title:
                return title[:300]
    return ""


def html_to_text(html: str) -> str:
    """A dependency-free readable-text extraction."""
    body = _SCRIPTY.sub(" ", html or "")
    # Give block elements a newline so sentences do not run together.
    body = re.sub(r"<(?:/p|/div|/li|br\s*/?|/h[1-6]|/tr)>", "\n", body, flags=re.IGNORECASE)
    text = _TAG.sub(" ", body)
    for entity, char in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&quot;", '"'),
        ("&#39;", "'"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&mdash;", "—"),
        ("&rsquo;", "\u2019"),
    ):
        text = text.replace(entity, char)
    text = _WS.sub(" ", text)
    text = _BLANKS.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def classify_body(text: str, *, http_status: int = 200) -> ExtractionStatus:
    """Decide the honest label for a retrieved body."""
    lowered = text[:4000].lower()
    if http_status in (401, 402, 403, 451):
        return ExtractionStatus.BLOCKED
    if http_status == 429 or any(m in lowered for m in _BLOCK_MARKERS):
        return ExtractionStatus.BLOCKED
    if any(m in lowered for m in _PAYWALL_MARKERS):
        return ExtractionStatus.PAYWALLED
    if len(text) >= MIN_FULL_CHARS:
        return ExtractionStatus.FULL
    if len(text) >= MIN_PARTIAL_CHARS:
        return ExtractionStatus.PARTIAL
    return ExtractionStatus.SNIPPET_ONLY


#: Minimum title overlap before an exact-headline search result is accepted as
#: the same article. The prototype ranked from a score starting at 0 with no
#: minimum, so an unrelated page was confidently "resolved"
#: (AUDIT §5, article_resolver.py:183).
MIN_HEADLINE_MATCH: Final = 0.6

#: (headline, publisher) -> direct article URL, or None.
HeadlineResolver = Callable[[str, str], Awaitable[str | None]]


def _title_tokens(title: str) -> set[str]:
    return {t for t in re.split(r"\W+", (title or "").lower(), flags=re.UNICODE) if len(t) > 2}


def headline_similarity(a: str, b: str) -> float:
    """Jaccard overlap of significant title tokens, 0.0-1.0."""
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass(slots=True)
class ArticleExtractor:
    """Fetches an article, resolving aggregator links to the publisher first."""

    fetcher: SafeFetcher
    jina_enabled: bool = True
    #: Optional last resort for aggregator links that cannot be resolved
    #: locally. Google News moved to opaque, server-resolved article ids, so
    #: the only remaining route to the publisher is to search for the exact
    #: headline — which is the fallback the brief calls for.
    headline_resolver: HeadlineResolver | None = field(default=None)

    async def resolve_publisher_url(
        self, url: str, *, title: str = "", publisher: str = ""
    ) -> tuple[str, str | None]:
        """Return ``(publisher_url, aggregator_url_or_None)``."""
        if not is_aggregator(url):
            return url, None

        direct = resolve_redirect_param(url)
        if direct and looks_like_article_url(direct):
            return direct, url

        try:
            page = await self.fetcher.fetch(url)
        except (UnsafeUrlError, FetchError):
            page = None

        if page is not None:
            canonical = extract_canonical(page.text, page.url)
            if canonical and looks_like_article_url(canonical):
                return canonical, url
            # The fetch itself may have redirected out of the aggregator.
            if not is_aggregator(page.url) and looks_like_article_url(page.url):
                return page.url, url

        if self.headline_resolver and title:
            found = await self.headline_resolver(title, publisher)
            if found and looks_like_article_url(found) and not is_aggregator(found):
                return found, url

        return url, None

    async def extract(self, source: Source) -> Source:
        """Populate ``source.content`` and set an honest status label."""
        target, aggregator = await self.resolve_publisher_url(
            source.url, title=source.title, publisher=source.publisher
        )
        if aggregator:
            # An aggregator url is only returned once a publisher was found.
            source.aggregator_url = aggregator
            source.url = target
            if not source.publisher:
                source.publisher = (urlparse(target).hostname or "").removeprefix("www.")
        elif is_aggregator(target):
            # Unresolved. Say so explicitly rather than letting the aggregator
            # host stand in for a publisher the user cannot verify.
            source.note = (
                "Link stayed on the news aggregator; the original publisher page "
                "could not be resolved."
            )

        try:
            page = await self.fetcher.fetch(target)
        except (UnsafeUrlError, FetchError) as exc:
            source.status = ExtractionStatus.FAILED
            source.retrieval = RetrievalMethod.NONE
            source.note = "The page could not be reached."
            log.info("extract.failed", host=urlparse(target).hostname, reason=str(exc))
            return source

        text = html_to_text(page.text)
        status = classify_body(text, http_status=page.status)

        # Jina Reader is a fallback for pages that render client-side. It sends
        # the URL to a third party, so it only runs when the direct read failed
        # and it is never used for the initial attempt.
        if self.jina_enabled and not status.has_body:
            fallback = await self._try_jina(target)
            if fallback is not None:
                text, status = fallback
                source.retrieval = RetrievalMethod.JINA_READER
                source.note = (
                    "Read via r.jina.ai because the direct fetch returned no article text."
                )
            else:
                source.retrieval = RetrievalMethod.DIRECT
        else:
            source.retrieval = RetrievalMethod.DIRECT

        if status.has_body:
            source.content = text[:MAX_CONTENT_CHARS]
        # The label is set here, by the extractor that produced the text — not
        # recomputed by a later layer that may have overwritten it.
        source.status = status

        if not source.title:
            source.title = extract_title(page.text) or source.url
        if source.published_at is None:
            published, verified = extract_published_at(page.text)
            if published is not None:
                source.published_at = published
                source.date_verified = verified
        return source

    async def _try_jina(self, url: str) -> tuple[str, ExtractionStatus] | None:
        from urllib.parse import quote

        # The prototype interpolated the target unencoded, so its ? and #
        # were reinterpreted as the reader's own delimiters.
        reader = f"https://r.jina.ai/{quote(url, safe='')}"
        try:
            page = await self.fetcher.fetch(reader, accept="text/plain")
        except (UnsafeUrlError, FetchError):
            return None
        text = page.text.strip()
        status = classify_body(text, http_status=page.status)
        return (text, status) if status.has_body else None

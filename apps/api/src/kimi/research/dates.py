"""Publication-date extraction and UTC normalisation.

The audit found two date defects in the prototype:

1. **Naive timestamps were forced to UTC** (``news_fallback.py:76-91``). A
   UTC+3 publisher's just-published article landed 3 hours in the future, failed
   the upper bound, and was silently discarded — squarely in this app's target
   Arabic use case.
2. **Only ``pubDate`` and a bare ``date`` were read** (``news_resilient.py:154``).
   Atom's ``published``/``updated`` and Dublin Core's ``dc:date`` were
   unreachable, so those feeds contributed nothing while reporting no error.

Here a naive timestamp is treated as *unverified* rather than assumed UTC, and
every common feed and HTML date field is parsed.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final

_ISO_CLEAN: Final = re.compile(r"(\.\d{3})\d+")

#: Order matters only for cost; all are tried.
_FORMATS: Final = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
    "%d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S %z",
    "%B %d, %Y",
    "%d %B %Y",
)


def parse_datetime(raw: str | None) -> tuple[datetime | None, bool]:
    """Parse a timestamp.

    Returns ``(value, verified)``. ``verified`` is False when the source gave no
    timezone or only a date, because in that case the instant is genuinely
    unknown to within a day — the caller must not present it as exact, and an
    explicit freshness window should not silently accept it.
    """
    if not raw:
        return None, False
    text = raw.strip()
    if not text:
        return None, False

    # RFC 2822 (RSS pubDate) — carries an offset when well-formed.
    try:
        value = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        value = None
    if value is not None:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC), False
        return value.astimezone(UTC), True

    candidate = _ISO_CLEAN.sub(r"\1", text.replace("Z", "+00:00"))
    try:
        value = datetime.fromisoformat(candidate)
    except ValueError:
        value = None
    if value is not None:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC), False
        return value.astimezone(UTC), True

    for fmt in _FORMATS:
        try:
            value = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if value.tzinfo is None:
            # Date-only or offset-less: real instant unknown, mark unverified.
            return value.replace(tzinfo=UTC), False
        return value.astimezone(UTC), True

    return None, False


#: Feed date fields, including the Atom and Dublin Core ones the prototype missed.
FEED_DATE_FIELDS: Final = (
    "pubDate",
    "published",
    "updated",
    "date",
    "{http://purl.org/dc/elements/1.1/}date",
    "{http://www.w3.org/2005/Atom}published",
    "{http://www.w3.org/2005/Atom}updated",
)

_META_PATTERNS: Final = tuple(
    re.compile(p, re.IGNORECASE | re.DOTALL)
    for p in (
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']article:published_time["\']',
        r'<meta[^>]+name=["\'](?:pubdate|publishdate|publish-date|date|dc\.date|'
        r'dcterms\.created|sailthru\.date)["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+itemprop=["\']datePublished["\'][^>]+content=["\']([^"\']+)',
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'<time[^>]+datetime=["\']([^"\']+)',
    )
)


def extract_published_at(html: str) -> tuple[datetime | None, bool]:
    """Find a publication date in a page's metadata."""
    if not html:
        return None, False
    for pattern in _META_PATTERNS:
        match = pattern.search(html)
        if not match:
            continue
        value, verified = parse_datetime(match.group(1))
        if value is not None:
            return value, verified
    return None, False


def humanise_age(published: datetime | None, now: datetime | None = None) -> str:
    if published is None:
        return "date unknown"
    delta = (now or datetime.now(UTC)) - published
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"

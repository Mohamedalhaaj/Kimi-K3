"""Query understanding: intent, topic, freshness window, and rewriting.

Two audited defects are fixed here, and both were pure-function bugs that a
handful of unit tests would have caught (docs/AUDIT.md §7).

**The "U" bug (news_resilient.py:105).** The prototype split the query on
``[\\n\\r.!?؟؛]+`` and kept only the first fragment, so "Give me the latest news
about U.S. tariffs" became the topic **"U"** and every provider was queried for
a single letter. The fix is not a better splitter — it is not to split at all.
Topic extraction here strips known lead-in phrases and freshness phrases and
keeps the remainder intact, so internal periods in U.S., U.N., No. 10, 3.5 and
example.com survive untouched.

**The 32-hour "24 hours" (news_fallback.py:104,116).** ``"d"`` mapped to
``timedelta(hours=30)`` and the upper bound allowed ``now + 2h``, giving a
32-hour window for a request that said 24. Windows here are exact, and the
future tolerance is a 5-minute clock-skew allowance that does not extend the
past cutoff.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

#: Clock skew only. This does NOT widen the window into the past.
FUTURE_TOLERANCE = timedelta(minutes=5)


class Intent(StrEnum):
    NEWS = "news"
    """Time-sensitive current events; freshness matters."""
    WEB = "web"
    """General factual lookup."""
    READ_URL = "read_url"
    """The user supplied a specific page to read."""
    NONE = "none"
    """No research needed."""


@dataclass(frozen=True, slots=True)
class FreshnessWindow:
    """A time constraint on results.

    ``explicit`` is the important field: when the user actually named a window,
    undated results are excluded rather than assumed recent, and the window is
    never widened.
    """

    hours: int | None
    explicit: bool
    label: str

    @property
    def requires_dates(self) -> bool:
        """An explicit window means an undated article cannot qualify."""
        return self.explicit and self.hours is not None

    def cutoff(self, now: datetime | None = None) -> datetime | None:
        if self.hours is None:
            return None
        return (now or datetime.now(UTC)) - timedelta(hours=self.hours)

    def contains(self, published: datetime | None, now: datetime | None = None) -> bool:
        """True when ``published`` falls inside the window."""
        current = now or datetime.now(UTC)
        if published is None:
            # Undated: allowed only when no explicit window was requested.
            return not self.requires_dates
        if published.tzinfo is None:
            raise ValueError("published must be timezone-aware")
        if published > current + FUTURE_TOLERANCE:
            return False
        floor = self.cutoff(current)
        return floor is None or published >= floor


UNBOUNDED: Final = FreshnessWindow(hours=None, explicit=False, label="any time")

# --------------------------------------------------------------------------
# Freshness parsing
# --------------------------------------------------------------------------

_ARABIC_DIGITS: Final = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

#: Ordered: the first match wins, so more specific patterns come first.
_EXPLICIT_WINDOWS: Final[list[tuple[re.Pattern[str], int, str]]] = [
    (
        re.compile(r"\b(?:last|past|previous|within(?:\s+the)?)\s+(\d{1,3})\s*(?:h|hrs?|hours?)\b"),
        0,
        "",
    ),
    (re.compile(r"\b(\d{1,3})\s*(?:h|hrs?|hours?)\s+ago\b"), 0, ""),
    (re.compile(r"(?:آخر|خلال|أخر)\s*(\d{1,3})\s*(?:ساعة|ساعات|س)\b"), 0, ""),
    (re.compile(r"\b(?:last|past|previous)\s+(\d{1,2})\s*(?:d|days?)\b"), -1, ""),
    (re.compile(r"(?:آخر|خلال|أخر)\s*(\d{1,2})\s*(?:يوم|أيام|ايام)\b"), -1, ""),
]

_NAMED_WINDOWS: Final[list[tuple[re.Pattern[str], int, str]]] = [
    (
        re.compile(r"\b(?:today|last\s+24\s*h(?:ours)?|past\s+24\s*h(?:ours)?)\b"),
        24,
        "last 24 hours",
    ),
    (re.compile(r"(?:اليوم|آخر\s*24\s*ساعة|خلال\s*24\s*ساعة)"), 24, "last 24 hours"),
    (re.compile(r"\byesterday\b"), 48, "last 48 hours"),
    (re.compile(r"\bأمس\b"), 48, "last 48 hours"),
    (re.compile(r"\b(?:this|last|past)\s+week\b"), 168, "last 7 days"),
    (re.compile(r"(?:هذا\s*الأسبوع|آخر\s*أسبوع|الأسبوع\s*الماضي)"), 168, "last 7 days"),
    (re.compile(r"\b(?:this|last|past)\s+month\b"), 720, "last 30 days"),
    (re.compile(r"(?:هذا\s*الشهر|آخر\s*شهر|الشهر\s*الماضي)"), 720, "last 30 days"),
]

#: "latest"/"recent" express a preference, not a constraint. They get a default
#: window that is reported to the user but is NOT treated as explicit — the
#: prototype silently mapped "latest" to 8 days.
_SOFT_RECENCY: Final = re.compile(
    r"\b(?:latest|recent|current|breaking|newest|now|up[- ]to[- ]date)\b"
    r"|(?:أحدث|آخر\s*الأخبار|الأخيرة|عاجل)"
)

DEFAULT_SOFT_HOURS: Final = 168


def parse_freshness(text: str) -> FreshnessWindow:
    """Extract the freshness window the user asked for.

    An explicit numeric or named window is exact. A vague "latest" produces a
    default window flagged ``explicit=False`` so the caller can say which window
    it actually used instead of pretending the user chose it.
    """
    lowered = text.lower().translate(_ARABIC_DIGITS)

    for pattern, unit, _ in _EXPLICIT_WINDOWS:
        match = pattern.search(lowered)
        if match:
            value = int(match.group(1))
            hours = value if unit == 0 else value * 24
            hours = max(1, min(hours, 24 * 365))
            label = f"last {value} hours" if unit == 0 else f"last {value} days"
            return FreshnessWindow(hours=hours, explicit=True, label=label)

    for pattern, hours, label in _NAMED_WINDOWS:
        if pattern.search(lowered):
            return FreshnessWindow(hours=hours, explicit=True, label=label)

    if _SOFT_RECENCY.search(lowered):
        return FreshnessWindow(
            hours=DEFAULT_SOFT_HOURS, explicit=False, label="last 7 days (default)"
        )

    return UNBOUNDED


# --------------------------------------------------------------------------
# Intent
# --------------------------------------------------------------------------

_URL_RE: Final = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)

_NEWS_TERMS: Final = re.compile(
    r"\b(?:news|headlines?|breaking|reported|reports|announced|developments?|"
    r"latest|updates?|happening|crisis|election|protest|casualt|airstrike)\b"
    r"|(?:أخبار|عاجل|تطورات|مستجدات|أعلن|تقارير|انتخابات|احتجاج)"
)

_LOOKUP_TERMS: Final = re.compile(
    r"\b(?:who|what|when|where|which|how|why|define|meaning|search|find|look up)\b"
    r"|(?:من\s+هو|ما\s+هي|ما\s+هو|متى|أين|كيف|لماذا|ابحث)"
)


def extract_urls(text: str) -> list[str]:
    """Return explicit http(s) URLs.

    Bare-domain guessing is deliberately not done. The prototype's
    ``BARE_DOMAIN_PATTERN`` matched any dotted token, so asking "what does app.py
    do?" triggered a real fetch of https://app.py.
    """
    seen: list[str] = []
    for raw in _URL_RE.findall(text):
        cleaned = raw.rstrip(".,;:!?)]}'\"")
        if cleaned not in seen:
            seen.append(cleaned)
    return seen


def classify_intent(text: str) -> Intent:
    if extract_urls(text):
        return Intent.READ_URL
    freshness = parse_freshness(text)
    if _NEWS_TERMS.search(text.lower()):
        return Intent.NEWS
    if freshness.hours is not None and freshness.explicit:
        # "X in the last 24 hours" is a news request even without the word news.
        return Intent.NEWS
    if _LOOKUP_TERMS.search(text.lower()):
        return Intent.WEB
    return Intent.WEB


# --------------------------------------------------------------------------
# Topic extraction
# --------------------------------------------------------------------------

#: Lead-ins stripped from the front of a request. Ordered longest-first so
#: "give me the latest news about" wins over "give me".
_LEAD_INS: Final[list[re.Pattern[str]]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
        r"(?:give|get|show|find|fetch|tell|bring)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:all\s+)?(?:latest\s+|recent\s+|current\s+|breaking\s+)?"
        r"(?:news|headlines|updates|information|info|details)\s*"
        r"(?:about|on|regarding|concerning|for|from|re)?\s*",
        # Allows a run of nouns: "news", "news updates", "headlines and updates".
        r"^\s*(?:what(?:'s| is| are)?\s+)?(?:the\s+)?"
        r"(?:latest|recent|current|newest|breaking)\s+"
        r"(?:news|headlines|updates|developments)"
        r"(?:\s+(?:and\s+)?(?:news|headlines|updates|developments))*\s*"
        r"(?:about|on|regarding|concerning|for|from|in)?\s*",
        r"^\s*(?:search|look\s+up|google|research)\s+(?:for\s+)?(?:the\s+)?",
        r"^\s*(?:i\s+want|i\s+need|i'd\s+like)\s+(?:to\s+know\s+)?(?:about\s+)?",
        r"^\s*(?:tell\s+me\s+about)\s*",
        r"^\s*(?:what(?:'s| is| are)\s+happening\s+(?:in|with|at)?)\s*",
        # Arabic
        r"^\s*(?:ما\s+(?:هي|هو)\s+)?(?:آخر|أحدث)\s*(?:الأخبار|أخبار)\s*(?:عن|حول|في|من)?\s*",
        r"^\s*(?:أعطني|اعطني|أخبرني|اخبرني|ابحث\s+عن|أريد)\s*(?:عن|حول)?\s*",
        r"^\s*(?:ما\s+هي|ما\s+هو|ماذا\s+يحدث\s+في)\s*",
    )
]

#: Freshness phrases removed from the topic so they are not searched literally.
_FRESHNESS_PHRASES: Final[list[re.Pattern[str]]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:in|from|during|within|over)?\s*(?:the\s+)?(?:last|past|previous)\s+"
        r"\d{1,3}\s*(?:h|hrs?|hours?|d|days?|weeks?|months?)\b",
        r"\b(?:in|from|during)?\s*(?:the\s+)?(?:last|past|this)\s+(?:week|month|year|day)\b",
        r"\b(?:today|yesterday|right\s+now|so\s+far)\b",
        r"(?:آخر|خلال|أخر)\s*\d{1,3}\s*(?:ساعة|ساعات|يوم|أيام|ايام)",
        r"(?:اليوم|أمس|الآن|هذا\s*الأسبوع|الأسبوع\s*الماضي|هذا\s*الشهر)",
    )
]

_TRAILING_JUNK: Final = re.compile(r"[\s,;:.!?،؛؟\-—]+$")
_LEADING_JUNK: Final = re.compile(r"^[\s,;:.!?،؛؟\-—]+")
_WHITESPACE: Final = re.compile(r"\s+")


def extract_topic(text: str) -> str:
    """Reduce a request to its subject without destroying abbreviations.

    Critically, this never splits on ``.`` — U.S., U.N., No. 10, 3.5 and
    example.com all survive intact.
    """
    topic = unicodedata.normalize("NFKC", text or "").strip()
    # Never search a URL literally; the read path handles those.
    topic = _URL_RE.sub(" ", topic)

    for pattern in _LEAD_INS:
        new = pattern.sub("", topic, count=1)
        if new != topic:
            topic = new
            break

    for pattern in _FRESHNESS_PHRASES:
        topic = pattern.sub(" ", topic)

    topic = _WHITESPACE.sub(" ", topic)
    topic = _LEADING_JUNK.sub("", topic)
    topic = _TRAILING_JUNK.sub("", topic)

    # If stripping removed everything, fall back to the original request rather
    # than searching for an empty string.
    if not topic.strip():
        fallback = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text or "")).strip()
        return _TRAILING_JUNK.sub("", fallback)
    return topic.strip()


# --------------------------------------------------------------------------
# Query planning
# --------------------------------------------------------------------------

_SITE_RE: Final = re.compile(r"\bsite:([a-z0-9.-]+\.[a-z]{2,})\b", re.IGNORECASE)


@dataclass(slots=True)
class ResearchPlan:
    """Everything the pipeline needs, decided once, up front."""

    original: str
    intent: Intent
    topic: str
    freshness: FreshnessWindow
    queries: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    is_arabic: bool = False


_ARABIC_RANGE: Final = re.compile(r"[؀-ۿݐ-ݿ]")


def plan_research(text: str, *, max_queries: int = 3) -> ResearchPlan:
    """Turn a user message into a concrete research plan."""
    intent = classify_intent(text)
    freshness = parse_freshness(text)
    urls = extract_urls(text)

    domains = [m.lower() for m in _SITE_RE.findall(text)]
    stripped = _SITE_RE.sub(" ", text)
    topic = extract_topic(stripped)
    is_arabic = bool(_ARABIC_RANGE.search(topic))

    queries: list[str] = []

    def add(q: str) -> None:
        q = _WHITESPACE.sub(" ", q).strip()
        if q and q.lower() not in {x.lower() for x in queries} and len(queries) < max_queries:
            queries.append(q)

    add(topic)
    if intent is Intent.NEWS and len(topic.split()) <= 8:
        # A second angle for breadth, in the language the user used.
        add(f"{topic} {'أخبار' if is_arabic else 'news'}")
        if freshness.hours is not None and freshness.hours <= 48:
            add(f"{topic} {'اليوم' if is_arabic else 'today'}")

    return ResearchPlan(
        original=text,
        intent=intent,
        topic=topic,
        freshness=freshness,
        queries=queries,
        urls=urls,
        domains=domains,
        is_arabic=is_arabic,
    )

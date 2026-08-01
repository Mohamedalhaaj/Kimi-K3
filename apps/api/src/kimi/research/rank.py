"""Deduplication, scoring and diversity.

Dedup uses two keys, as the prototype did — canonical URL and a normalised
title — because aggregators republish the same story under different URLs and
syndication republishes it under different titles.

Scoring is transparent and additive rather than learned: authority, recency and
extraction completeness. A source that could not be read scores lower than one
that could, which keeps unreadable results from crowding out usable ones without
hiding them.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlparse

from kimi.research.extract import canonicalise, is_aggregator
from kimi.research.models import ExtractionStatus, Source

#: Well-known outlets with editorial process. Not exhaustive and not a
#: statement about accuracy — only a tie-breaker so a content farm does not
#: outrank a wire service on an identical story.
_AUTHORITY: Final[dict[str, float]] = {
    "reuters.com": 1.0,
    "apnews.com": 1.0,
    "bbc.com": 0.95,
    "bbc.co.uk": 0.95,
    "aljazeera.com": 0.9,
    "aljazeera.net": 0.9,
    "ft.com": 0.9,
    "economist.com": 0.9,
    "nytimes.com": 0.88,
    "washingtonpost.com": 0.88,
    "theguardian.com": 0.88,
    "wsj.com": 0.88,
    "bloomberg.com": 0.88,
    "cnn.com": 0.8,
    "npr.org": 0.8,
    "france24.com": 0.8,
    "dw.com": 0.8,
    "arabnews.com": 0.75,
    "alarabiya.net": 0.75,
    "middleeasteye.net": 0.7,
    "libyaobserver.ly": 0.7,
    "libyaherald.com": 0.7,
}
_DEFAULT_AUTHORITY: Final = 0.5

_STATUS_WEIGHT: Final[dict[ExtractionStatus, float]] = {
    ExtractionStatus.FULL: 1.0,
    ExtractionStatus.PARTIAL: 0.75,
    ExtractionStatus.SNIPPET_ONLY: 0.45,
    ExtractionStatus.PAYWALLED: 0.35,
    ExtractionStatus.BLOCKED: 0.2,
    ExtractionStatus.FAILED: 0.1,
}

_TITLE_NOISE: Final = re.compile(r"[^\w\s]", re.UNICODE)
_WS: Final = re.compile(r"\s+")


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def title_key(title: str) -> str:
    """A normalised title for cross-provider dedup."""
    text = unicodedata.normalize("NFKC", title or "").lower()
    text = _TITLE_NOISE.sub(" ", text)
    return _WS.sub(" ", text).strip()[:140]


def authority(url: str) -> float:
    host = host_of(url)
    if host in _AUTHORITY:
        return _AUTHORITY[host]
    # Match a parent domain, e.g. edition.cnn.com -> cnn.com
    for known, value in _AUTHORITY.items():
        if host.endswith("." + known):
            return value
    return _DEFAULT_AUTHORITY


def recency(
    published: datetime | None, *, now: datetime | None = None, window_hours: int | None
) -> float:
    """1.0 for brand new, decaying to 0 at the edge of the window."""
    if published is None:
        return 0.25
    current = now or datetime.now(UTC)
    age_hours = max(0.0, (current - published).total_seconds() / 3600)
    horizon = float(window_hours or 168)
    return max(0.0, 1.0 - min(age_hours / horizon, 1.0))


def _better(challenger: Source, incumbent: Source) -> bool:
    """Which of two copies of the same story to keep."""
    if _STATUS_WEIGHT[challenger.status] != _STATUS_WEIGHT[incumbent.status]:
        return _STATUS_WEIGHT[challenger.status] > _STATUS_WEIGHT[incumbent.status]
    if challenger.date_verified != incumbent.date_verified:
        return challenger.date_verified
    if challenger.published_at and incumbent.published_at:
        return challenger.published_at > incumbent.published_at
    return challenger.published_at is not None and incumbent.published_at is None


def dedupe(sources: list[Source]) -> list[Source]:
    """Collapse duplicates, keeping the best-extracted copy of each story."""
    by_key: dict[str, Source] = {}
    order: list[str] = []

    for source in sources:
        keys = [f"u:{canonicalise(source.url)}"]
        tk = title_key(source.title)
        if tk:
            keys.append(f"t:{tk}")

        existing_key = next((k for k in keys if k in by_key), None)
        if existing_key is None:
            for k in keys:
                by_key[k] = source
            order.append(keys[0])
            continue

        incumbent = by_key[existing_key]
        # Prefer, in order: a readable body, a verified date, then the newer
        # timestamp. The last one matters because a syndicated copy can carry a
        # stale date, which would wrongly push the story out of the window.
        challenger_better = _better(source, incumbent)
        if challenger_better:
            for k in keys:
                by_key[k] = source
            for i, existing in enumerate(order):
                if by_key.get(existing) is incumbent:
                    order[i] = keys[0]
                    break

    seen: set[int] = set()
    result: list[Source] = []
    for key in order:
        winner = by_key.get(key)
        if winner is not None and id(winner) not in seen:
            seen.add(id(winner))
            result.append(winner)
    return result


#: Penalty for a link still pointing at a news aggregator.
#:
#: Google News moved to opaque, server-resolved article ids, so such a link may
#: never resolve to a publisher. A source we cannot attribute, read, or let the
#: user verify is genuinely less useful than a direct one, so it ranks lower —
#: but it is not excluded, because sometimes it is the only coverage there is.
AGGREGATOR_PENALTY: Final = 0.30


def score_sources(
    sources: list[Source], *, window_hours: int | None, now: datetime | None = None
) -> list[Source]:
    for source in sources:
        score = (
            0.40 * recency(source.published_at, now=now, window_hours=window_hours)
            + 0.35 * authority(source.url)
            + 0.25 * _STATUS_WEIGHT[source.status]
        )
        if is_aggregator(source.url):
            score -= AGGREGATOR_PENALTY
        source.score = round(max(0.0, score), 4)
    return sources


def diversify(sources: list[Source], *, limit: int, max_per_domain: int = 2) -> list[Source]:
    """Cap per-publisher representation so one outlet cannot fill the answer.

    The cap is strict: returning three sources from two publishers is more
    honest than four sources that are really one newsroom repeated. The single
    exception is a story only one publisher covered — there, capping would mean
    discarding the only coverage that exists, so the cap is lifted.
    """
    ranked = sorted(sources, key=lambda s: s.score, reverse=True)
    distinct_domains = {host_of(s.url) for s in ranked}
    if len(distinct_domains) <= 1:
        return ranked[:limit]

    counts: dict[str, int] = defaultdict(int)
    picked: list[Source] = []
    for source in ranked:
        host = host_of(source.url)
        if counts[host] >= max_per_domain:
            continue
        counts[host] += 1
        picked.append(source)
        if len(picked) >= limit:
            break
    return picked


def sort_newest_first(sources: list[Source]) -> list[Source]:
    """Newest first; undated last rather than treated as ancient or fresh."""
    dated = [s for s in sources if s.published_at is not None]
    undated = [s for s in sources if s.published_at is None]
    dated.sort(key=lambda s: s.published_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return dated + undated

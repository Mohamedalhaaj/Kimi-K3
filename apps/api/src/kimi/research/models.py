"""Source records.

Every source carries an explicit :class:`ExtractionStatus`. The brief requires
that the user can always tell a fully-read article from a headline, and the
audit found the prototype computed that label one layer *below* the layer that
last mutated the content, so a "Full article read" source could be silently
clobbered by a paywall stub (``web_tools.py:710-713``).

Here the label is set by the extractor that produced the text and is only ever
changed by re-running extraction on the same record.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ExtractionStatus(StrEnum):
    FULL = "full"
    """The article body was read."""
    PARTIAL = "partial"
    """Some body text was read, but it looks truncated or thin."""
    SNIPPET_ONLY = "snippet_only"
    """Only the provider's headline/snippet is available."""
    PAYWALLED = "paywalled"
    BLOCKED = "blocked"
    """The publisher refused the request (403/429/bot wall)."""
    FAILED = "failed"

    @property
    def label(self) -> str:
        return {
            ExtractionStatus.FULL: "Full article read",
            ExtractionStatus.PARTIAL: "Partial article read",
            ExtractionStatus.SNIPPET_ONLY: "Headline/snippet only",
            ExtractionStatus.PAYWALLED: "Paywalled",
            ExtractionStatus.BLOCKED: "Blocked",
            ExtractionStatus.FAILED: "Could not be read",
        }[self]

    @property
    def has_body(self) -> bool:
        return self in (ExtractionStatus.FULL, ExtractionStatus.PARTIAL)


class RetrievalMethod(StrEnum):
    DIRECT = "direct"
    JINA_READER = "jina_reader"
    PROVIDER_SNIPPET = "provider_snippet"
    NONE = "none"


class Source(BaseModel):
    """One retrieved source, with full provenance."""

    url: str
    title: str
    publisher: str = ""
    snippet: str = ""
    content: str = ""

    published_at: datetime | None = None
    #: True when the date came from the page/feed rather than being guessed.
    date_verified: bool = False

    provider: str = ""
    """Which search provider surfaced this result."""
    retrieval: RetrievalMethod = RetrievalMethod.NONE
    status: ExtractionStatus = ExtractionStatus.SNIPPET_ONLY

    #: Set when the original result pointed at an aggregator.
    aggregator_url: str | None = None
    note: str = ""

    score: float = 0.0

    def excerpt(self, limit: int = 320) -> str:
        text = (self.content or self.snippet or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rsplit(" ", 1)[0] + "…"

    def to_citation(self, index: int) -> dict[str, object]:
        """The record shown in the sources panel."""
        return {
            "index": index,
            "title": self.title,
            "publisher": self.publisher,
            "url": self.url,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "date_verified": self.date_verified,
            "provider": self.provider,
            "retrieval": str(self.retrieval),
            "status": str(self.status),
            "status_label": self.status.label
            + ("" if self.date_verified or self.published_at is None else " · Date unverified"),
            "excerpt": self.excerpt(),
            "aggregator_url": self.aggregator_url,
            "note": self.note,
        }


class SearchResults(BaseModel):
    """A provider's contribution, including how it went."""

    provider: str
    sources: list[Source] = Field(default_factory=list)
    ok: bool = True
    error: str = ""
    duration_ms: float = 0.0

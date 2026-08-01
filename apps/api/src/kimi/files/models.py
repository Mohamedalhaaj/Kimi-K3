"""Parsed-document records.

The design goal is *citability*. The prototype flattened every file into one
text blob, so the model could say "the contract states X" with no way for the
user to check where. Here text is carried as :class:`Segment` objects that each
know their page, slide, sheet or row, so an answer can point at Page 12 and the
user can go look.

The second goal is that a file is **never silently absent**. The prototype
dropped any upload whose extracted text was empty (``file_tools.py:261``), so a
scanned PDF vanished from the prompt entirely — the model never learned the file
existed — while the UI cheerfully showed a paperclip and "Read 1 attachment(s)".
Every document here produces a summary even when no text could be read.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DocumentKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    CSV = "csv"
    TEXT = "text"
    IMAGE = "image"
    UNKNOWN = "unknown"


class ParseStatus(StrEnum):
    PARSED = "parsed"
    """Text was extracted successfully."""
    PARTIAL = "partial"
    """Some content was extracted, but the file was truncated or partly unreadable."""
    NO_TEXT_LAYER = "no_text_layer"
    """A scanned/image-only PDF. Pages exist but carry no selectable text."""
    ENCRYPTED = "encrypted"
    PASSWORD_REQUIRED = "password_required"  # noqa: S105 - a status, not a secret
    UNSUPPORTED = "unsupported"
    TOO_LARGE = "too_large"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return {
            ParseStatus.PARSED: "Read in full",
            ParseStatus.PARTIAL: "Read in part",
            ParseStatus.NO_TEXT_LAYER: "Scanned — no text layer",
            ParseStatus.ENCRYPTED: "Encrypted",
            ParseStatus.PASSWORD_REQUIRED: "Password required",
            ParseStatus.UNSUPPORTED: "Unsupported file type",
            ParseStatus.TOO_LARGE: "Too large",
            ParseStatus.FAILED: "Could not be read",
        }[self]

    @property
    def has_text(self) -> bool:
        return self in (ParseStatus.PARSED, ParseStatus.PARTIAL)


class RefKind(StrEnum):
    PAGE = "page"
    SLIDE = "slide"
    SHEET = "sheet"
    ROW = "row"
    SECTION = "section"
    NOTES = "notes"
    TABLE = "table"
    WHOLE = "whole"


@dataclass(slots=True)
class SegmentRef:
    """Where in the document a piece of text came from."""

    kind: RefKind
    number: int = 0
    name: str = ""

    @property
    def label(self) -> str:
        if self.kind is RefKind.WHOLE:
            return "Document"
        if self.kind is RefKind.SHEET:
            return f"Sheet {self.name}" if self.name else f"Sheet {self.number}"
        if self.kind is RefKind.NOTES:
            return f"Slide {self.number} notes"
        if self.kind is RefKind.TABLE:
            base = f"Table {self.number}"
            return f"{base} ({self.name})" if self.name else base
        return f"{self.kind.value.capitalize()} {self.number}"


@dataclass(slots=True)
class Segment:
    """One citable chunk of a document."""

    ref: SegmentRef
    text: str
    truncated: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "ref": {
                "kind": str(self.ref.kind),
                "number": self.ref.number,
                "name": self.ref.name,
                "label": self.ref.label,
            },
            "text": self.text,
            "truncated": self.truncated,
        }


def new_document_id() -> str:
    """Server-generated. The uploaded filename is never used as an identifier."""
    return uuid.uuid4().hex[:16]


@dataclass(slots=True)
class ParsedDocument:
    """A parsed upload, with provenance and an honest status."""

    id: str
    filename: str
    """Display only, already sanitised. Never used to build a path."""
    kind: DocumentKind
    status: ParseStatus
    mime_type: str = ""
    size_bytes: int = 0

    segments: list[Segment] = field(default_factory=list)
    #: Always populated, even when no text could be read.
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    #: Set only for images, which travel to the model as a data URL.
    image_data_url: str = ""

    @property
    def text_chars(self) -> int:
        return sum(len(s.text) for s in self.segments)

    def to_payload(self, *, include_text: bool = True) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "kind": str(self.kind),
            "status": str(self.status),
            "status_label": self.status.label,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "summary": self.summary,
            "metadata": self.metadata,
            "warnings": self.warnings,
            "text_chars": self.text_chars,
            "segment_count": len(self.segments),
            "segments": [s.to_payload() for s in self.segments] if include_text else [],
            "has_image": bool(self.image_data_url),
        }

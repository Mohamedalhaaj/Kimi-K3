"""File type detection and upload safety.

The filename is never trusted. It is used for display after sanitising, and for
nothing else — not for the storage path, not for the document id, and only as a
tie-breaker for type detection when the content itself is ambiguous. The brief
requires this explicitly, and it is what stops ``../../etc/passwd`` or a ``.pdf``
that is really a zip bomb from mattering.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from kimi.files.models import DocumentKind

#: 25 MB matches the ceiling the prototype's uploader advertised.
MAX_UPLOAD_BYTES: Final = 25 * 1024 * 1024
MAX_FILENAME_CHARS: Final = 120

#: Content signatures, checked before any extension.
_MAGIC: Final[tuple[tuple[bytes, DocumentKind], ...]] = (
    (b"%PDF-", DocumentKind.PDF),
    (b"\x89PNG\r\n\x1a\n", DocumentKind.IMAGE),
    (b"\xff\xd8\xff", DocumentKind.IMAGE),  # JPEG
    (b"GIF87a", DocumentKind.IMAGE),
    (b"GIF89a", DocumentKind.IMAGE),
    (b"BM", DocumentKind.IMAGE),
)

_TEXT_SUFFIXES: Final = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".rst",
        ".log",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".xml",
        ".html",
        ".htm",
        ".css",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".rs",
        ".go",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".sh",
        ".sql",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".srt",
        ".vtt",
    }
)

_OOXML: Final[dict[str, DocumentKind]] = {
    ".docx": DocumentKind.DOCX,
    ".pptx": DocumentKind.PPTX,
    ".xlsx": DocumentKind.XLSX,
    ".xlsm": DocumentKind.XLSX,
}

_IMAGE_SUFFIXES: Final = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"})

_MIME: Final[dict[DocumentKind, str]] = {
    DocumentKind.PDF: "application/pdf",
    DocumentKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    DocumentKind.PPTX: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    DocumentKind.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    DocumentKind.CSV: "text/csv",
    DocumentKind.TEXT: "text/plain",
    DocumentKind.IMAGE: "image/*",
    DocumentKind.UNKNOWN: "application/octet-stream",
}

_UNSAFE_NAME = re.compile(r"[^\w\s.\-()\[\]؀-ۿ]", re.UNICODE)
_DOTS = re.compile(r"\.{2,}")


def sanitise_filename(raw: str) -> str:
    """Return a display-safe filename.

    Strips directory components, collapses runs of dots that could form ``..``,
    and removes control and separator characters. Arabic is preserved — this app
    treats Arabic filenames as first class.
    """
    name = unicodedata.normalize("NFKC", raw or "").strip()
    # Take the basename under both separators; never trust the client.
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _DOTS.sub(".", name)
    name = _UNSAFE_NAME.sub("_", name).strip(" .")
    if not name:
        return "untitled"
    if len(name) > MAX_FILENAME_CHARS:
        stem, _, suffix = name.rpartition(".")
        keep = MAX_FILENAME_CHARS - len(suffix) - 1
        name = f"{stem[:keep]}.{suffix}" if suffix and keep > 0 else name[:MAX_FILENAME_CHARS]
    return name


def suffix_of(filename: str) -> str:
    name = sanitise_filename(filename).lower()
    return f".{name.rpartition('.')[2]}" if "." in name else ""


def _looks_like_zip(head: bytes) -> bool:
    return head[:2] == b"PK"


def _looks_textual(sample: bytes) -> bool:
    """A cheap printable-ratio test; NUL bytes mean binary."""
    if not sample:
        return True
    if b"\x00" in sample[:4096]:
        return False
    printable = sum(1 for b in sample[:4096] if b in (9, 10, 13) or 32 <= b < 127 or b >= 128)
    return printable / min(len(sample), 4096) > 0.85


def detect_kind(filename: str, data: bytes) -> DocumentKind:
    """Classify by content first, extension second."""
    head = data[:16]

    for magic, kind in _MAGIC:
        if head.startswith(magic):
            return kind

    suffix = suffix_of(filename)

    # OOXML files are all zips; only the extension distinguishes them, so this
    # is the one place the name is consulted — and it is still bounded by the
    # zip signature above it.
    if _looks_like_zip(head):
        return _OOXML.get(suffix, DocumentKind.UNKNOWN)

    if suffix == ".csv" or suffix == ".tsv":
        return DocumentKind.CSV
    if suffix in _IMAGE_SUFFIXES:
        return DocumentKind.IMAGE
    if suffix in _TEXT_SUFFIXES:
        return DocumentKind.TEXT

    # Unknown extension but plainly textual: treat as text rather than refusing.
    if _looks_textual(data):
        return DocumentKind.TEXT
    return DocumentKind.UNKNOWN


def mime_for(kind: DocumentKind) -> str:
    return _MIME.get(kind, "application/octet-stream")


def is_supported(kind: DocumentKind) -> bool:
    return kind is not DocumentKind.UNKNOWN

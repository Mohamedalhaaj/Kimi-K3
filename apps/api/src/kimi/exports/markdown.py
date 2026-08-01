"""A small Markdown reader for export.

Deliberately not a full CommonMark implementation. It recognises exactly the
constructs the model actually emits — headings, lists, tables, fenced code,
block quotes, links and inline emphasis — because the alternative was the
prototype's approach of splitting on blank lines and writing every line as a
plain paragraph, which shipped literal ``## Heading`` and pipe soup into Word
(AUDIT §8, export_tools.py:56-60).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final


class BlockKind(StrEnum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    BULLET = "bullet"
    NUMBERED = "numbered"
    CODE = "code"
    QUOTE = "quote"
    TABLE = "table"
    RULE = "rule"


@dataclass(slots=True)
class Span:
    """An inline run. ``href`` set means it is a link."""

    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    href: str = ""


@dataclass(slots=True)
class Block:
    kind: BlockKind
    spans: list[Span] = field(default_factory=list)
    #: Heading depth, or list nesting depth.
    level: int = 0
    #: TABLE only: rows of cells, each cell a list of spans.
    rows: list[list[list[Span]]] = field(default_factory=list)
    #: CODE only.
    text: str = ""
    language: str = ""


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_FENCE = re.compile(r"^```(\w*)\s*$")
_RULE = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")

#: Inline: code, bold, italic, link. Code first so its content is left alone.
_INLINE: Final = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<bold>\*\*[^*]+\*\*|__[^_]+__)"
    r"|(?P<italic>\*[^*\n]+\*|_[^_\n]+_)"
    r"|(?P<link>\[[^\]]+\]\([^)\s]+\))"
)
_LINK_PARTS = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def parse_inline(text: str) -> list[Span]:
    """Split a line into styled runs."""
    spans: list[Span] = []
    cursor = 0

    for match in _INLINE.finditer(text):
        if match.start() > cursor:
            spans.append(Span(text[cursor : match.start()]))
        raw = match.group(0)
        if match.group("code"):
            spans.append(Span(raw[1:-1], code=True))
        elif match.group("bold"):
            spans.append(Span(raw[2:-2], bold=True))
        elif match.group("italic"):
            spans.append(Span(raw[1:-1], italic=True))
        elif match.group("link"):
            parts = _LINK_PARTS.match(raw)
            if parts:
                spans.append(Span(parts.group(1), href=parts.group(2)))
            else:  # pragma: no cover - the regex guarantees a match
                spans.append(Span(raw))
        cursor = match.end()

    if cursor < len(text):
        spans.append(Span(text[cursor:]))
    return spans or [Span(text)]


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [c.strip() for c in stripped.split("|")]


def parse_markdown(source: str) -> list[Block]:
    """Parse ``source`` into a flat list of blocks."""
    lines = (source or "").replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    paragraph: list[str] = []
    index = 0

    def flush() -> None:
        if paragraph:
            text = " ".join(paragraph).strip()
            if text:
                blocks.append(Block(BlockKind.PARAGRAPH, spans=parse_inline(text)))
            paragraph.clear()

    while index < len(lines):
        line = lines[index]

        if fence := _FENCE.match(line):
            flush()
            language = fence.group(1)
            index += 1
            body: list[str] = []
            while index < len(lines) and not _FENCE.match(lines[index]):
                body.append(lines[index])
                index += 1
            index += 1  # closing fence
            blocks.append(Block(BlockKind.CODE, text="\n".join(body), language=language))
            continue

        if not line.strip():
            flush()
            index += 1
            continue

        if _RULE.match(line):
            flush()
            blocks.append(Block(BlockKind.RULE))
            index += 1
            continue

        if heading := _HEADING.match(line):
            flush()
            blocks.append(
                Block(
                    BlockKind.HEADING,
                    spans=parse_inline(heading.group(2).strip()),
                    level=len(heading.group(1)),
                )
            )
            index += 1
            continue

        # A table needs a header row followed by a separator row.
        if "|" in line and index + 1 < len(lines) and _TABLE_SEP.match(lines[index + 1]):
            flush()
            rows = [[parse_inline(c) for c in _split_row(line)]]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append([parse_inline(c) for c in _split_row(lines[index])])
                index += 1
            blocks.append(Block(BlockKind.TABLE, rows=rows))
            continue

        if bullet := _BULLET.match(line):
            flush()
            blocks.append(
                Block(
                    BlockKind.BULLET,
                    spans=parse_inline(bullet.group(2)),
                    level=len(bullet.group(1)) // 2,
                )
            )
            index += 1
            continue

        if numbered := _NUMBERED.match(line):
            flush()
            blocks.append(
                Block(
                    BlockKind.NUMBERED,
                    spans=parse_inline(numbered.group(2)),
                    level=len(numbered.group(1)) // 2,
                )
            )
            index += 1
            continue

        if quote := _QUOTE.match(line):
            flush()
            blocks.append(Block(BlockKind.QUOTE, spans=parse_inline(quote.group(1))))
            index += 1
            continue

        paragraph.append(line.strip())
        index += 1

    flush()
    return blocks


_ARABIC = re.compile(r"[؀-ۿݐ-ݿ]")


def is_rtl(text: str) -> bool:
    """True when the text is predominantly right-to-left."""
    rtl = len(_ARABIC.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return rtl > latin

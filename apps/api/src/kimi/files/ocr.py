"""Optional OCR for scanned PDFs.

The brief is explicit: use OCR **only when required**, never on every page by
default. So this runs at exactly one point — when a PDF has pages but no
selectable text at all — and it is bounded by a page cap and a wall-clock
deadline, because OCR is orders of magnitude slower than text extraction.

Every dependency here is optional. When tesseract or the render library is
missing, :func:`ocr_available` is False and the caller reports the PDF as
scanned-and-unread rather than pretending it succeeded.
"""

from __future__ import annotations

import io
import time
from functools import lru_cache
from typing import Final

import structlog

log = structlog.get_logger(__name__)

#: OCR is slow; cap the work so an upload cannot hang a turn.
MAX_OCR_PAGES: Final = 20
OCR_DEADLINE_S: Final = 60.0
#: Rendering DPI. 200 is the usual accuracy/speed sweet spot for prose.
RENDER_DPI: Final = 200
#: Arabic first — this app targets Arabic as a first-class language.
DEFAULT_LANGS: Final = "ara+eng"


#: Only a POSITIVE result is cached. Caching the negative would let one
#: transient failure — a half-finished reinstall, a momentarily missing PATH —
#: disable OCR for the entire life of the process. Observed exactly once during
#: development, immediately after `uv sync` replaced pytesseract mid-run.
_OCR_READY = False


def ocr_available() -> bool:
    """True when both the renderer and a working tesseract are present."""
    global _OCR_READY
    if _OCR_READY:
        return True
    try:
        import pypdfium2  # noqa: F401
        import pytesseract

        pytesseract.get_tesseract_version()
    except Exception:
        return False
    _OCR_READY = True
    return True


@lru_cache(maxsize=1)
def available_languages() -> frozenset[str]:
    try:
        import pytesseract

        return frozenset(pytesseract.get_languages(config=""))
    except Exception:
        return frozenset()


def _langs() -> str:
    """Use only language packs that are actually installed."""
    installed = available_languages()
    wanted = [lang for lang in DEFAULT_LANGS.split("+") if lang in installed]
    return "+".join(wanted) or "eng"


def ocr_pdf(data: bytes, *, max_pages: int = MAX_OCR_PAGES) -> list[tuple[int, str]]:
    """OCR a scanned PDF.

    Returns ``(page_number, text)`` for pages that produced text. Raises nothing:
    a failure yields an empty list and the caller reports the PDF as unread.
    """
    if not ocr_available():
        return []

    import pypdfium2
    import pytesseract
    from PIL import Image

    started = time.perf_counter()
    langs = _langs()
    out: list[tuple[int, str]] = []

    try:
        document = pypdfium2.PdfDocument(io.BytesIO(data))
    except Exception as exc:
        log.warning("ocr.open_failed", exc_type=type(exc).__name__)
        return []

    try:
        limit = min(len(document), max_pages)
        for index in range(limit):
            if time.perf_counter() - started > OCR_DEADLINE_S:
                log.warning("ocr.deadline", pages_done=len(out))
                break
            try:
                page = document[index]
                bitmap = page.render(scale=RENDER_DPI / 72)
                image: Image.Image = bitmap.to_pil()
                text = pytesseract.image_to_string(image, lang=langs).strip()
            except Exception as exc:
                log.warning("ocr.page_failed", page=index + 1, exc_type=type(exc).__name__)
                continue
            if len(text) >= 12:
                out.append((index + 1, text))
    finally:
        document.close()

    log.info(
        "ocr.completed",
        pages_with_text=len(out),
        languages=langs,
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return out

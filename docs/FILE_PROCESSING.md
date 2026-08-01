# File processing

Supported: PDF, DOCX, PPTX, XLSX/XLSM, CSV/TSV, plain text and code, SRT/VTT,
and images (PNG, JPEG, WebP, GIF, BMP, AVIF).

## Citability

Text is carried as **segments** that know where they came from — page, slide,
sheet, section, table, or notes — so an answer can say "Table 1 of
libya-report.docx" and you can check it. The prototype flattened every file into
one blob.

## Audited defects fixed

| Defect | Fix |
|---|---|
| Scanned PDFs vanished from the prompt entirely (`file_tools.py:261`) | detected, declared, and OCR'd when available |
| `reader.decrypt` never called | encrypted PDFs reported, not silently empty |
| PPTX lost tables, groups, notes (`hasattr(shape,"text")` is False for a `GraphicFrame`) | shapes walked recursively; notes are their own segment |
| DOCX emitted all paragraphs then all tables | body walked in document order |
| CSV reported its 500-row sample as the total | true row count; delimiter sniffed |
| Eight unmarked hard slices | every truncation appends `[… truncated]` |
| Budget charged body only | labels and separators charged too |

## OCR

Runs at exactly one point: a PDF with pages but **zero** selectable text. The
brief forbids OCRing every page by default, and a test proves a PDF with a real
text layer never reaches the OCR path.

- `ara+eng`, filtered to installed language packs.
- 20-page cap, 60s deadline.
- Output is labelled machine-recognised so it is not mistaken for authoritative.
- Optional: without tesseract the file is reported scanned-and-unread rather
  than silently empty.

```bash
brew install tesseract tesseract-lang
cd apps/api && uv sync --extra ocr
```

**Verified:** an image-only PDF was read correctly in English. Arabic OCR is
enabled and the pack is installed, but accuracy on real Arabic scans is **not
verified** — the synthetic test fixture could not render shaped Arabic.

## Safety

Type is decided by content magic bytes, not the extension. Filenames are
sanitised for display only and never used for a path or an id. **File bytes are
never written to disk** — only parsed text is stored. Attachments are scoped to
their conversation. 25 MB cap.

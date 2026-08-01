# Test report

All commands below were executed on macOS 15 (Apple Silicon), Python 3.13.2,
Node 24.16.0. Every result is copied from a real run.

## Automated — API

```bash
cd apps/api && uv run pytest -q
```

**379 passed, 0 failed.**

| File | Tests | Covers |
|---|---:|---|
| `test_browser_safety.py` | 58 | Card/OTP/key refusal by shape, credential labels in English + Arabic, resolved-label classification, CAPTCHA refusal, approval gating |
| `test_research_query.py` | 44 | Abbreviation preservation (U.S., U.N., No. 10), exact 24h boundary, undated exclusion, Arabic windows and digits |
| `test_calculator.py` | 35 | Nested-exponent DoS, boolean rejection, non-arithmetic refusal |
| `test_files.py` | 35 | Scanned-PDF detection, OCR, DOCX order, PPTX tables/notes, CSV row counts, truncation sentinels, path traversal |
| `test_research_extract.py` | 34 | Homepage rejection, canonical resolution, paywall/block classification |
| `test_research_net.py` | 28 | SSRF, DNS rebinding, IMDS v4/v6, scheme allowlist, DNS pinning |
| `test_toolrouter.py` | 27 | Zero-model guarantee, routing conservatism, tool lifecycle |
| `test_research_pipeline.py` | 20 | Window enforcement, dedup, diversity, partial results, prompt fencing |
| `test_exports.py` | 18 | Real Word headings/tables/hyperlinks/bibliography, RTL, JSON round-trip |
| `test_provider.py` | 15 | SSE parsing, retry policy, vision capability, user-safe errors |
| `test_tool_registry.py` | 13 | Contract invariants, timeout, approval, error shaping |
| `test_projects_search.py` | 12 | Project cascade delete, FTS injection safety, index sync |
| `test_chat_stream.py` | 10 | Partial-output preservation, no orphan turns, secret redaction |
| `test_files_api.py` | 10 | Upload, cross-conversation isolation, document-to-model wiring |
| `test_context.py` | 8 | Token budgeting, image retention |
| `test_conversations.py` | 7 | CRUD, SQL-injection-shaped search, cascade |
| `test_health.py` | 5 | Liveness/readiness split, model discovery |

```bash
cd apps/api && uv run ruff check .          # All checks passed
cd apps/api && uv run ruff format --check . # 78 files already formatted
cd apps/api && uv run mypy                  # no issues in 55 source files
cd apps/api && uvx pip-audit --strict       # No known vulnerabilities
```

## Automated — Web

```bash
cd apps/web && npm run typecheck && npm run lint && npm run check:contrast
cd apps/web && npm run build
cd apps/web && npm audit --omit=dev         # found 0 vulnerabilities
```

All pass. The contrast gate covers 22 text/surface pairs across light and dark.

**Security finding fixed during Phase 11:** `sharp` carried four high-severity
libvips CVEs and `postcss` three more. `npm audit fix --force` proposed
downgrading Next.js from 16 to **9.3.3** — not a fix. Both were pinned forward
with npm `overrides` instead, leaving Next.js 16 intact. Result: 0
vulnerabilities, build passing.

## Manual — executed against the live stack

Backend on `127.0.0.1:8787`, frontend on `localhost:3000`, real TokenRouter key.

| # | Check | Result |
|---|---|---|
| 1 | `GET /healthz` | `{"status":"ok"}` |
| 2 | `GET /readyz` | `200`, `database.ok=true`, `model_provider.ok=true` |
| 3 | `GET /models` | 5 models with capability sets and context windows |
| 4 | Conversation create → rename → list → delete | all correct; deleted id returns `404` |
| 5 | **Rename latency** | **3.1 ms**, and `provider.calls == []` — no model request |
| 6 | Real streaming turn (API) | `"STREAM OK"`, usage `249` tokens, `error=None`, persisted |
| 7 | Browser round trip, English | "Name the capital of Libya in one word." → **"Tripoli"**, metadata `19.6s to first token · 19.6s total · 279 tokens · moonshotai/kimi-k3-free` |
| 8 | Browser round trip, Arabic | "ما هي عاصمة ليبيا؟" → **"طرابلس"** |
| 9 | **Per-message direction** | English messages `dir="ltr"`, Arabic prompt *and* answer `dir="rtl"`, in the same conversation |
| 10 | Pending state | "Waiting for the model · 4s" with live clock — **not** an empty assistant card |
| 11 | Enter to send | verified via a dispatched `keydown`; composer cleared, message appended |
| 12 | Stop button | replaces Send during streaming |
| 13 | Dark mode | verified by screenshot; accent lifted, no flash on reload |
| 14 | Responsive | sidebar collapses to a drawer at 529 px; full rail at 1280 px |
| 15 | Composer overlap | never overlaps the last message at any tested width |
| 16 | `./scripts/start-local.sh` from cold | both services ready, **1.56 s**, exit 0, returns to prompt |
| 17 | Secrets | `.env` absent from all 33 commits; blob scan for `sk-…` returns 0 matches |
| 18 | Legacy app preserved | all 12 modules byte-compile; monkeypatch chain resolves identically after the move |

## Live verification (Phase 15)

Cold start of the whole stack: **1.07s**, exit 0.

| Check | Result |
|---|---|
| `/readyz` | ready, database + provider OK |
| `/models` | 1 model — exactly what the key can call |
| `/api/tools` | 13 registered; 8 deterministic, 2 approval-gated |
| `calculator 25*4` | `100` in **1.8ms**, `model_called=False` |
| `calculator sqrt(2)*pi` | `4.44288293816` in **1.2ms**, `model_called=False` |
| `browser_click "Buy now"` | `waiting_for_approval` — handler never ran |
| Browser multi-step | open example.com 1.8s → click "Learn more" 2.3s (no cold start) → back 0.9s, history preserved |
| OCR on an image-only PDF | English read correctly in 0.5s |
| Documents in chat | Word table cited as "Table 1", sheet as "Sheet Q3", scanned PDF declared unreadable |
| News, last 24 hours | window `{hours: 24, explicit: true}`, 18 results dropped out-of-window |
| Arabic news request | same 24h window parsed, `topic="أخبار ليبيا"` |

## Not tested

Honest gaps:

- **No Playwright end-to-end suite.** The live checks above were driven
  manually through real sessions rather than by a committed spec.
- **No load or soak testing.**
- **Docker not installed**, so no container path was verified.
- **No screen-reader testing.** ARIA roles, labels, `aria-live` and keyboard
  focus were implemented and inspected in the accessibility tree, which is not
  the same as testing with VoiceOver.
- **Arabic OCR accuracy** — pipeline verified, accuracy on real scans not.

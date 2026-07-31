# Test report

All commands below were executed on macOS 15 (Apple Silicon), Python 3.13.2,
Node 24.16.0. Every result is copied from a real run.

## Automated — API

```bash
cd apps/api && uv run pytest -q
```

**42 passed, 0 failed.**

| File | Tests | Covers |
|---|---:|---|
| `tests/test_provider.py` | 13 | SSE parsing, malformed chunks, 401/429/5xx mapping, bounded retry, timeout, user-safe error text, vision refusal before the network call, bearer header, unknown-model capability default |
| `tests/test_chat_stream.py` | 10 | Streaming order, partial-output preservation, no orphan user turns, secret redaction, typed SSE errors, image refusal, mode presets, untrusted-content system prompt |
| `tests/test_context.py` | 8 | Token-budget bounding, newest-turn survival, image retention, preset differentiation, report self-consistency |
| `tests/test_conversations.py` | 7 | CRUD, pin ordering, SQL-injection-shaped search, cascade delete, clear-context, deterministic rename |
| `tests/test_health.py` | 4 | Liveness vs readiness separation, request-id echo, capability advertisement |

```bash
cd apps/api && uv run ruff check .      # All checks passed!
cd apps/api && uv run ruff format --check .   # 26 files already formatted
cd apps/api && uv run mypy              # Success: no issues found in 20 source files
```

mypy runs in `strict` mode with `disallow_untyped_defs`.

## Automated — Web

```bash
cd apps/web && npm run typecheck   # exit 0, no output
cd apps/web && npm run lint        # exit 0, no output
cd apps/web && npm run check:contrast
cd apps/web && npm run build
```

Contrast gate: **all 22 text/surface pairs meet WCAG AA** across light and dark.
Build: `✓ Compiled successfully`, routes `/` and `/_not-found` prerendered.

The contrast gate caught a real defect during development: `--color-fg-subtle`
was `#7b8496`, which is **3.76:1** on white — below AA for the placeholder and
hint text it is used for. It is now `#6a7280` (4.85:1 on the content surface,
4.56:1 on the sidebar).

Design detector (`impeccable/scripts/detect.mjs --json apps/web/app apps/web/components`):
**`[]` — zero findings.**

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

## Not tested

Honest gaps, because the features do not exist yet:

- Web/news research, browser agent, file upload, exports, projects — Phase 5+.
- No Playwright end-to-end suite yet; item 7–15 above were driven manually
  through a real Chromium session rather than by a committed spec.
- No load or soak testing.
- Docker is not installed on this machine, so no container path was verified.
- Screen-reader testing was not performed; ARIA roles, labels, `aria-live`, and
  keyboard focus were implemented and inspected in the accessibility tree, which
  is not the same as testing with VoiceOver.

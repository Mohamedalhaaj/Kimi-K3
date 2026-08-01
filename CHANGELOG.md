# Changelog

## [2.0.0-alpha] — 2026-07-31

First stage of the rebuild. Phases 0–4 of the plan are complete and verified;
phases 5–15 have not been started.

### Added

- **Monorepo** — `apps/api` (FastAPI), `apps/web` (Next.js 16), `docs/`,
  `scripts/`, with the prototype preserved unmodified in `legacy_streamlit/`.
- **FastAPI backend** — app factory, lifespan, CORS allowlist, per-request
  correlation ids, structured `structlog` logging with URL/header redaction.
- **Error taxonomy** — `KimiError` with stable codes distinguishing model
  failure, auth, rate limit, timeout, cancellation, unsupported capability,
  not-found, and internal errors, each with a user-safe message.
- **Database** — SQLAlchemy 2.0 async models (Project/Conversation/Message) on
  SQLite with `aiosqlite`, PostgreSQL-compatible, `PRAGMA foreign_keys=ON` and
  WAL applied per connection.
- **Provider layer** — `ChatProvider` protocol and a TokenRouter implementation
  on `httpx` with explicit timeout, bounded retry, and true cancellation. A
  static capability registry refuses images to text-only models before any
  network call.
- **Context budgeting** — history bounded by an estimated token budget with a
  `ContextReport` surfaced to the UI.
- **Mode presets** — Fast / Balanced / Deep configure output tokens, history
  ratio, turn cap, temperature, and image retention.
- **SSE chat endpoint** — `start` / `delta` / `warning` / `error` / `done`.
- **Conversation API** — create, list (with search and pagination), get, patch
  (rename/pin/model/mode), delete, clear messages.
- **Next.js chat interface** — sidebar with search/pin/rename/delete, streaming
  message area, docked composer, model and mode selectors, stop, regenerate,
  copy, light/dark/system themes.
- **Arabic support** — per-message direction via the Unicode first-strong
  heuristic, ignoring URLs, code, and digits.
- **WCAG contrast gate** — `apps/web/scripts/contrast.mjs` fails the build if any
  text/surface pair drops below AA.
- **One-command local start** — `./scripts/start-local.sh` (cold start 1.56s).
- **CI** — lint, format, strict types, tests, contrast, build, and a committed
  secrets check.
- **Docs** — `AUDIT.md`, `ARCHITECTURE.md`, `SECURITY.md`, `TEST_REPORT.md`,
  `MIGRATION_FROM_STREAMLIT.md`, `PRODUCT.md`, `DESIGN.md`.

### Fixed (relative to the prototype)

- Partial assistant output is no longer erased by an error; it is persisted and
  the error is stored separately (`app.py:719-725`).
- A failed or cancelled turn can no longer leave an orphaned user message.
- Prompt size is bounded by tokens rather than message count, ending the re-send
  of up to 660,000 characters of stale scraped text per turn.
- Raw provider exceptions no longer reach the user, the transcript, or exports.
- All timestamps are timezone-aware; `datetime.utcnow` is gone.
- `.gitignore` no longer permits `.env.*` variants, root `secrets.toml`, `venv/`,
  `env/`, local databases, or generated conversation exports to be committed.
- Conversation rename is deterministic and makes no model call (3.1 ms measured).

### Not carried over

`core/__init__.py`'s process-wide monkeypatches, `core/browser_direct.py`'s SDK
class surgery, `core/browser_speed.py`, and two dead news pipelines. See
[docs/MIGRATION_FROM_STREAMLIT.md](docs/MIGRATION_FROM_STREAMLIT.md).

### Known issues

Web research, browser agent, file intelligence, artifacts, and projects are not
yet ported and remain available only in `legacy_streamlit/`, along with their
recorded defects. No Docker path is verified. No authentication.

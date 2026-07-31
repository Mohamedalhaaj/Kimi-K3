# Kimi Workspace 2

A local-first AI research workspace: streaming chat over configurable models,
with a bias toward saying plainly what it did and did not verify.

This repository is mid-rebuild. The original Streamlit prototype is preserved,
unmodified, in [`legacy_streamlit/`](legacy_streamlit/). What is described below
is what actually runs today — nothing here documents an unbuilt feature.

---

## Status

| Phase | State |
|---|---|
| 0 — Audit of the prototype | Done — [docs/AUDIT.md](docs/AUDIT.md) |
| 1 — Monorepo, legacy preserved | Done |
| 2 — FastAPI backend + database | Done |
| 3 — Model provider + streaming | Done |
| 4 — Next.js chat interface | Done |
| 5–15 — Tools, research, browser agent, files, projects, artifacts, deploy | **Not started** |

The prototype's web research, browser agent, and file parsing still live only in
`legacy_streamlit/`. They have **not** been ported yet. See
[Known limitations](#known-limitations).

## Quick start

Requires macOS/Linux, [uv](https://docs.astral.sh/uv/), and Node 20+.

```bash
cp .env.example .env
```

Add your `TOKENROUTER_API_KEY` to `.env`, then:

```bash
./scripts/start-local.sh
```

Opens the app on <http://localhost:3000> and the API on <http://127.0.0.1:8787>.

```bash
./scripts/stop-local.sh
```

## What works today

- **Streaming chat** over any OpenAI-compatible model via TokenRouter, with
  real SSE, a working Stop button, and per-turn cancellation.
- **Persistent conversations** in SQLite — create, rename, pin, search, delete,
  clear context. Survives a restart.
- **Measured telemetry** on every answer: time to first token, total time, token
  usage, model id. Nothing is estimated or fabricated.
- **Response modes** (Fast / Balanced / Deep) that change output length, history
  budget, retained turns, temperature, and image retention — not just
  `max_tokens`.
- **Capability-aware models**: an image is never sent to a text-only model, and
  the refusal is shown to the user rather than silently dropping the attachment.
- **Arabic and English** with per-message direction detection.
- **Light / dark / system** themes with no flash on load.

## Architecture

```
apps/
  api/                 FastAPI + SQLAlchemy 2.0 (async) + SQLite/PostgreSQL
    src/kimi/
      providers/       ChatProvider protocol + TokenRouter implementation
      services/        context budgeting
      routers/         health, conversations, chat (SSE)
      db/              models + session
  web/                 Next.js 16, React 19, Tailwind 4, TypeScript strict
    components/        Workspace, Sidebar, Composer, MessageView
    lib/               api client, SSE reader, direction detection, theme
legacy_streamlit/      the original prototype, unmodified
docs/                  audit, architecture, security, test report
scripts/               start-local.sh, stop-local.sh
```

Full detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Environment variables

Only one is required. See [.env.example](.env.example) for the rest.

| Variable | Required | Default |
|---|---|---|
| `TOKENROUTER_API_KEY` | **yes** | — |
| `TOKENROUTER_BASE_URL` | no | `https://api.tokenrouter.com/v1` |
| `DEFAULT_MODEL` | no | `moonshotai/kimi-k3-free` |
| `DATABASE_URL` | no | `sqlite+aiosqlite:///data/kimi.db` |
| `ENVIRONMENT` | no | `local` |
| `DEBUG` | no | `false` |
| `CORS_ORIGINS` | no | `http://localhost:3000` |
| `NEXT_PUBLIC_API_URL` | no | `http://127.0.0.1:8787` |

## Testing

```bash
cd apps/api && uv run pytest -q
```

```bash
cd apps/api && uv run mypy
```

```bash
cd apps/web && npm run check
```

```bash
cd apps/web && npm run build
```

Results and evidence: [docs/TEST_REPORT.md](docs/TEST_REPORT.md).

## Security notes

- `.env` is gitignored and **has never been committed** — verified by scanning
  every blob reachable from every ref.
- Raw exception text never reaches the client or the transcript; errors are a
  typed taxonomy with user-safe messages.
- Query strings are never logged (they carry signed-URL tokens).
- The system prompt marks all tool/web/document content as untrusted data.
- CI fails if a secrets file becomes tracked or a key-shaped string is committed.

More in [docs/SECURITY.md](docs/SECURITY.md).

## Known limitations

These are real gaps, stated up front rather than left to be discovered:

1. **No tools yet.** Web search, news research, the browser agent, file upload,
   exports, and projects exist only in `legacy_streamlit/`. Phase 5 onward.
2. **No Docker.** Docker is not installed on the development machine, so a
   compose file would ship unverified. Local run is the supported path.
3. **The free Kimi model does not stream incrementally.** Measured time to first
   token is 17–20s and `first_token_ms ≈ total_ms`, i.e. the provider returns
   the whole completion at once. The streaming path itself is correct — verified
   against a mocked incremental provider in the test suite — but on this model
   you will see a pause and then the full answer. Faster models stream normally.
4. **No authentication.** The app binds to `127.0.0.1` and is single-user.
5. **Token counts used for budgeting are estimated** (characters ÷ 2.6,
   deliberately conservative for Arabic). Displayed usage always comes from the
   provider.
6. **No screenshots in this README** — the app must be run to be seen.

## Legacy app

`legacy_streamlit/` still runs and is documented in
[legacy_streamlit/README.md](legacy_streamlit/README.md), including the hazards
found in the audit (in particular: do not run an import sorter over
`core/__init__.py`).

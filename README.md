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
| 5 — Tool registry | Done — [docs/TOOLS.md](docs/TOOLS.md) |
| 6 — Web & news research | Done — [docs/WEB_RESEARCH.md](docs/WEB_RESEARCH.md) |
| 7 — Browser agent | Done — [docs/BROWSER_AGENT.md](docs/BROWSER_AGENT.md) |
| 8 — File & image intelligence | Done — [docs/FILE_PROCESSING.md](docs/FILE_PROCESSING.md) |
| 9 — Projects, memory, search | Done |
| 10 — Artifacts & exports | Done |
| 11 — Security hardening | Done — [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) |
| 12 — Automated testing | Done — 379 tests |
| 13 — Performance | Measured, see below |
| 14 — Deployment & docs | Local path done; no Docker |
| 15 — Final validation | Done — [docs/TEST_REPORT.md](docs/TEST_REPORT.md) |

Everything the prototype could do has been ported and its audited defects
fixed. `legacy_streamlit/` remains as a reference and fallback.

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

- **Tools** — a typed registry with 13 tools. Deterministic tools (calculator,
  browser navigation) return their result with **zero model calls**;
  consequential ones (browser click/type) require explicit approval.
- **Web & news research** — four providers, exact freshness windows, aggregator
  resolution, and every source labelled with how much of it was actually read.
- **Browser agent** — a persistent, isolated Chromium that is not your Chrome.
  It never types credentials and refuses CAPTCHAs.
- **Documents** — PDF, Word, PowerPoint, Excel, CSV, text and images, with
  page/slide/sheet citations and OCR for scanned PDFs.
- **Projects and search** — FTS5 across conversations *and* document contents.
- **Exports** — Word with real headings, tables, hyperlinks and a sources
  bibliography; also Markdown, JSON, CSV, XLSX.
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
      tools/           registry, engine, calculator, web, browser
      research/        providers, query, extraction, ranking, resilience
      files/           parsers, detection, OCR
      exports/         markdown reader + DOCX/XLSX writers
      browser/         persistent Chromium session + safety policy
      services/        context budgeting, tool routing
      routers/         health, conversations, chat, tools, files, exports,
                       projects, search
      db/              models, session, migrations
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

Real gaps, stated up front rather than left to be discovered:

1. **Only one model is available** to the configured key, and it is a reasoning
   model. Measured time to first token is 17–43s and `first_token_ms ≈
   total_ms` — it returns the whole answer at once rather than streaming. The
   streaming path itself is verified correct against a mocked incremental
   provider; faster models stream normally. `/models` now lists only what the
   key can actually call.
2. **No Docker.** Docker is not installed on the development machine, so a
   compose file would ship unverified. Local run is the supported path.
3. **No authentication or rate limiting.** Binds `127.0.0.1`, single-user.
4. **Arabic OCR accuracy is unverified.** The `ara` pack is installed and
   enabled, and English OCR is verified working; the synthetic test fixture
   could not render shaped Arabic, so no claim is made about real Arabic scans.
5. **Google News links are not always resolvable** to a publisher — the article
   ids are opaque and server-resolved (verified by decoding). What cannot be
   resolved is labelled as still on the aggregator rather than letting the
   aggregator pose as the publisher.
6. **No Playwright end-to-end suite.** Behaviour is covered by 379 unit and
   integration tests plus manual live verification against real pages.
7. **Budgeting token counts are estimated** (characters ÷ 2.6, deliberately
   conservative for Arabic). Displayed usage always comes from the provider.
8. **`legacy_streamlit/` is unhardened** and retains every finding in AUDIT §5–6.
9. **No screenshots in this README** — the app must be run to be seen.

## Legacy app

`legacy_streamlit/` still runs and is documented in
[legacy_streamlit/README.md](legacy_streamlit/README.md), including the hazards
found in the audit (in particular: do not run an import sorter over
`core/__init__.py`).

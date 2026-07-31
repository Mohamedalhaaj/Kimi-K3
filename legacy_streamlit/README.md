# Legacy Streamlit app (preserved)

This is the original Kimi Workspace prototype, moved here unmodified during the
Kimi Workspace 2 rebuild. It is kept as a **reference and fallback**, not as the
primary interface. No file inside this directory has been edited — only relocated.

## Run it

From the repository root:

```bash
cd legacy_streamlit && ../.venv/bin/streamlit run app.py
```

It reads `TOKENROUTER_API_KEY` from the repository-root `.env`.

## Verified-working behaviour preserved here

Import resolution after `core/__init__.py` installs its patches (confirmed by
executing the import):

| Symbol | Resolves to |
|---|---|
| `web_tools.browse_web` | `browser_speed…browse_with_compact_browser_results` → `browser_agent…browse_with_browser_commands` → original |
| `web_tools.search_news` | `core.search_news_resilient` |
| `web_tools.fetch_public_page` | `browser_agent…fetch_with_browser_fallback` |

## Known defects — do not "fix" them here

`docs/AUDIT.md` records the full audit. The load-bearing hazards:

- **Do not run an import sorter over `core/__init__.py`.** Lines 24-25 are E402
  violations, and the patches at lines 20-22 must execute before them. Reordering
  silently disables the Playwright fallback with no error and no test failure.
- `/browser inspect`, `/browser back`, `/browser reload` raise `IndexError`
  (`core/browser_direct.py:65`).
- `/browser` reports success even when Playwright never ran
  (`core/browser_direct.py:70-76`).
- `core/calculator.py:50` — nested exponentiation such as `(((10**99)**99)**99)`
  hangs the process for every session.
- A "last 24 hours" news request is enforced as a **32-hour** window
  (`core/news_fallback.py:104,116`).
- `core/news_resilient.py:105` splits queries on `.`, so "U.S. tariffs" searches
  for the topic `U`.
- `core/browser_direct.py` mutates `openai.OpenAI.__init__` and the shared
  `Completions` class **process-wide**, affecting every OpenAI client in the
  interpreter.

These are fixed properly in the rebuilt application rather than patched here.

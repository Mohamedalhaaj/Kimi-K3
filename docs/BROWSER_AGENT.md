# Browser agent

A persistent, isolated Chromium session driven through Playwright. It is **not**
your Chrome: the profile lives in the gitignored `data/browser-profile/`, and no
existing browser session is touched.

## Running it

Playwright is an optional extra:

```bash
cd apps/api && uv sync --extra browser && uv run playwright install chromium
```

Without it the tools report an actionable install message rather than failing
obscurely.

## What changed from the prototype

| Prototype | Now |
|---|---|
| Chromium launched and closed **per command** | one long-lived context |
| `_navigate` before every click, discarding page state | navigation only when asked |
| Profile at `~/.kimi-workspace` — machine-global | per-installation under `data/` |
| `networkidle` waits on pages that never idle | bounded `domcontentloaded` + settle |
| Nine silent `except: pass` blocks | every failure logged |
| No audit trail | every action logged with its invocation id |

Measured on a live page: open 1.8s (cold start), click 2.3s, back 0.9s. The
click and back pay no start-up because the browser stays open.

## Safety

The prototype's guards were 13- and 12-entry **English substring** denylists
checked against the *user's query*. Three consequences: `click Continue` could
activate "Continue to payment"; `type Email :: 482913` typed an OTP; every
non-English label was invisible.

- The **resolved element's own label** is classified, not the query.
- Typed values are classified by **shape** — Luhn-checked card numbers, 4–8
  digit codes, private keys, seed phrases — so a credential is caught in any
  language.
- Credential field labels are recognised in English **and Arabic**.
- `browser_click` and `browser_type` require explicit approval.
- CAPTCHAs are refused outright.
- `fill()` never submits. `accept_downloads=False`.
- Typed values are never logged — only the field label and its length.
- Page text is fenced as untrusted before it reaches the model.

For authentication, use the visible browser yourself. The agent will not type
your password.

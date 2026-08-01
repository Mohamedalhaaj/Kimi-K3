# Tool registry

The audit's root-cause finding was *patch-the-symbol instead of change-the-seam*:
with no registry and no interface, the prototype added its third tool by mutating
module attributes from a package `__init__`, and its LLM bypass by mutating the
OpenAI SDK's `Completions` class at runtime (AUDIT §7).

Dispatch here is a dictionary lookup. There is no `if tool_id == …` chain
anywhere.

## The contract

Every tool is a `ToolSpec` declaring:

| Field | Purpose |
|---|---|
| `id`, `name`, `description` | identity and the model-facing schema |
| `input_model`, `output_model` | Pydantic types; validation happens in the engine |
| `deterministic` | the tool's output **is** the answer — no model call |
| `requires_model_followup` | the model must write prose from the result |
| `timeout_s`, `cancellable` | enforced by the engine, not the handler |
| `permission` | `SAFE` / `READ_PUBLIC` / `LOCAL` / `CONSEQUENTIAL` |
| `requires_approval` | blocks execution until the user approves |
| `renderer`, `error_renderer` | which UI component draws the result |
| `audit_event` | emitted on every invocation |

Two invariants are enforced **at registration**, not left to review:

1. `deterministic` and `requires_model_followup` cannot both be true. That
   combination is exactly how the prototype came to send thousands of tokens
   after a completed browser click.
2. A `CONSEQUENTIAL` tool must require approval.

## States

`queued → running → completed | completed_with_warnings | failed | cancelled`,
plus `waiting_for_approval`. All seven are rendered distinctly in the UI, and
"completed" appears only once the tool has actually finished.

## The tools

| id | Deterministic | Approval | Notes |
|---|---|---|---|
| `calculator` | yes | – | AST allowlist, no `eval`. Zero tokens. |
| `open_public_url` | no | – | SSRF-pinned fetch |
| `read_article` | no | – | resolves aggregator → publisher |
| `web_search` | no | – | multi-provider, cited |
| `news_search` | no | – | exact freshness window |
| `browser_open` | yes | – | persistent Chromium |
| `browser_back` / `_forward` / `_reload` | yes | – | no model call |
| `browser_links` | yes | – | visible links |
| `browser_inspect` | no | – | page text, fenced as untrusted |
| `browser_click` | yes | **yes** | resolved label is classified |
| `browser_type` | yes | **yes** | refuses credentials by shape |

## Timing

`duration_ms` is the tool's own wall clock and is reported separately from
`first_token_ms` and `total_ms`. A deterministic answer is never labelled
"0.0s" when the tool actually took seconds.

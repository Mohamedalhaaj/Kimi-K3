# Architecture

## Why a rewrite rather than more patches

The audit ([AUDIT.md](AUDIT.md) §7) found one root cause behind all three of the
prototype's repeated patch cycles: **patch-the-symbol instead of change-the-seam.**
There was no tool registry, no dependency injection, no interface, and no
configuration object, so the third tool was added by mutating module attributes
from a package `__init__`, and the LLM bypass by mutating an SDK class at
runtime. Correctness ended up depending on statement ordering enforced by a
comment.

Every structural decision below exists to remove that class of failure.

## Shape

```
Browser (Next.js)  ──HTTP/JSON──►  FastAPI  ──►  ChatProvider  ──►  TokenRouter
      ▲                   │                          (protocol)
      └───── SSE ─────────┘
                          │
                          ▼
                    SQLAlchemy async ──► SQLite (or PostgreSQL)
```

Two processes, one database, no queue, no broker. The brief asked for something
that stays simple enough to run on a laptop, and nothing here needs more.

## Seams

| Seam | Contract | Why |
|---|---|---|
| `providers/base.py` | `ChatProvider` protocol + `StreamEvent` union | Adding a vendor is one new file. The rest of the app never imports a vendor SDK. |
| `errors.py` | `KimiError` taxonomy with stable `code` | The UI can distinguish a rate limit from a bad key from a cancellation without string-matching. |
| `services/context.py` | `build_context()` → messages + `ContextReport` | Prompt assembly is a pure function, unit-testable without a network or a database. |
| `deps.py` | FastAPI dependencies | Tests swap the provider with `dependency_overrides` — no monkeypatching. |

The prototype's `install_browser_patches` / `install_direct_browser_response_patch`
have no equivalent here by design.

## Request flow for one chat turn

1. `POST /api/chat/stream` validates the body against `ChatRequest` (Pydantic).
2. A session opens, the conversation is loaded, and the **user row is written
   and committed** with the next `seq`.
3. If this is `seq 0`, the conversation title is derived from the first line of
   the message — deterministic, no model call.
4. `build_context()` assembles the prompt newest-first under a token budget
   derived from the model's context window and the mode preset. It returns a
   `ContextReport` describing what was included and dropped.
5. The `start` SSE frame carries that report, so the UI can be honest about
   trimming instead of guessing.
6. `provider.stream_chat()` yields `TextDelta` events, forwarded as `delta`
   frames. Between each, `request.is_disconnected()` is checked.
7. In `finally` — reached on success, provider error, unhandled exception, or
   disconnect — the assistant row is written with whatever text arrived, plus
   `usage`, `timing`, and any `error` in **separate columns**.

Step 7 is the fix for the prototype's worst correctness bug: its `except` block
assigned the error string over `full_response`, erasing text the user had
already read (`app.py:719-725`).

## Data model

`Project → Conversation → Message`, string-UUID keys, timezone-aware UTC
timestamps everywhere (`datetime.utcnow` is deprecated and naive — the source of
the prototype's timezone bugs).

`Message.seq` gives a stable order independent of timestamp resolution and is
unique per conversation. Cascades are declared **both** on the ORM relationship
and as `ON DELETE CASCADE`, and SQLite is sent `PRAGMA foreign_keys=ON` on every
connection — without it SQLite silently ignores foreign keys and "delete a
project" would orphan its data.

## Context budgeting

`PRESETS` maps each mode to output tokens, a history budget *ratio* of the
model's context window, a turn cap, temperature, and how many recent user turns
keep their images.

Token estimation is `len(text) / 2.6`, deliberately conservative: English
averages ~4 characters per token but Arabic is far denser, and this app treats
Arabic as first-class. Under-estimating the budget truncates a little early;
over-estimating overflows the model. Displayed usage is always the provider's
number, never this estimate.

## Frontend

The main column is `grid-rows-[auto_minmax(0,1fr)_auto]`. The header, scroll
region, and composer are siblings, so the composer **cannot** overlap the last
message. This is a structural guarantee rather than bottom padding that has to
be kept in sync with a growing textarea.

Streaming is read off `fetch` rather than `EventSource`, because `EventSource`
cannot POST. The `AbortSignal` is what makes Stop real: aborting closes the
socket, the backend's disconnect check ends the provider call, and the partial
turn is still persisted.

Direction is decided per message by the Unicode first-strong heuristic after
stripping URLs, code spans, digits and punctuation — so a message that opens
with `https://…` is classified by its prose, not its link.

## Deliberate non-goals for this stage

No queue, no cache layer, no vector store, no auth, no multi-tenancy. Each would
be speculative until the tool layer (Phase 5) defines what actually needs them.

# Kimi Workspace 2 — product context

> Written from the explicit build brief rather than a live interview: the brief
> pins audience, brand, tone, and constraints, and the operator instructed that
> routine questions be decided rather than asked. Inferences are labelled.

## What it is

A local-first AI workspace: streaming chat with configurable models, tool
orchestration, current web research with verifiable citations, a controlled
local browser agent, and document/image analysis. It replaces a Streamlit
prototype (preserved in `legacy_streamlit/`).

## Who uses it

A researcher/analyst working in **Arabic and English**, on a Mac, often on
current-affairs material where a wrong date or an invented citation is a real
failure. They are category-fluent: they use ChatGPT and Claude daily and will
notice anything subtly off.

*Inferred from the brief's emphasis on Libya news, Arabic RTL as a first-class
requirement, and macOS Apple Silicon as the local target.*

## Use scene

Desk work, long sessions, indoor ambient light, frequently side-by-side with a
source document or a browser. **Light is the default surface; dark is a
first-class equal**, because long reading sessions run into the evening.

## The mode

**Operate.** The user is in a task. Success is a completed piece of research,
not an impressed visitor. Earned familiarity beats novelty; the tool should
disappear into the work.

## Non-negotiable product truths

1. **Never claim more than was verified.** Article extraction status, freshness
   windows, and citation provenance are shown, not implied.
2. **Deterministic actions never consult the model.** Renaming a conversation,
   a browser click, or a calculation returns as soon as the operation finishes.
3. **Nothing is silently dropped.** An image a model cannot read, a source that
   failed to fetch, or history trimmed for budget is reported to the user.
4. **Measured, not estimated.** Displayed timings and token counts come from the
   provider or a real clock, or are omitted.
5. **Arabic is not an afterthought.** Full RTL, correct mixed-direction
   punctuation, and per-message direction.

## Voice

Plain, specific, unhurried. Controls name their action. Errors name the problem
and the recovery. No emoji in product chrome, no exclamation marks, no
"Oops!". The product never congratulates itself.

## Explicit anti-goals (from the brief)

Excessive gradients, huge empty areas, oversized rounded cards everywhere,
visual gimmicks, layout clipping, composer overlap, content jumping, empty
assistant cards, buttons that look disabled after work completed, tool status
that sticks.

# Migration from the Streamlit prototype

## What moved

Nothing was deleted. `app.py`, `core/`, `requirements.txt`, and `.streamlit/`
were relocated to `legacy_streamlit/` with `git mv`, byte-for-byte unchanged.
It still runs:

```bash
cd legacy_streamlit && ../.venv/bin/streamlit run app.py
```

## Ported so far

| Prototype behaviour | Where it lives now | Change |
|---|---|---|
| Mode presets (`MODE_SETTINGS`) | `services/context.py:PRESETS` | Now also drive history ratio, turn cap, temperature, image retention — not only `max_tokens`. |
| Image budget (last 2 user turns) | `services/context.py` | Same idea, plus a hard capability check and a visible count of anything dropped. |
| Streaming with TTFT + usage | `providers/tokenrouter.py` | Timings measured with `perf_counter`; usage read from the final chunk as before. |
| Never blank assistant bubble | `components/Workspace.tsx` | Achieved by not rendering an empty row at all, plus a labelled pending state. |
| API-key gate | `config.py` + `/readyz` | Missing key is a typed error with a named fix, not a `st.stop()`. |
| Numbered-source contract | *not yet* | Phase 6. |

## Deliberately not ported

| Prototype module | Verdict | Reason |
|---|---|---|
| `core/__init__.py` | **Discarded** | A monkeypatch installer masquerading as a package init. It rebinds three `web_tools` functions and patches `openai.OpenAI.__init__` plus the shared `Completions` class process-wide. Its ordering is load-bearing and enforced only by a comment, so any import sorter silently disables the Playwright fallback. |
| `core/browser_direct.py` | **Discarded, idea kept** | The zero-LLM fast path is the right instinct and returns in the new design as an explicit branch in the app's own request path. The delivery — SDK class surgery, a confirmed `IndexError` on argument-less verbs, fabricated success when Playwright never ran, and a regex scrape that lets a visited page write directly into the transcript — is not salvageable. |
| `core/browser_speed.py` | **Discarded** | Its only surviving effect is truncating tool context, which belongs to a context-budget layer. Its model instruction is unreachable on the turn it fires. |
| `core/news_fallback.search_news_robust` | **Dead code** | Live for exactly one commit, replaced three commits later, never deleted. |
| `core/web_tools.search_news` | **Dead code** | Permanently shadowed by the assignment in `core/__init__.py:48`. |

## Bugs fixed rather than carried over

- Assistant text is no longer erased by an error. The prototype's `except`
  assigned the error string over the accumulated response (`app.py:719-725`).
- A failed turn can no longer orphan a user message and produce two consecutive
  user turns.
- Prompt size is bounded by tokens, not message count. The prototype re-sent
  every retained turn's full attachment and tool text on every request.
- Raw provider exceptions no longer reach the user, the transcript, or exports.
- Timestamps are timezone-aware; `datetime.utcnow` is not used.

## Still only in the legacy app

Web search, news research with freshness windows, article resolution, the
Playwright browser agent, PDF/DOCX/PPTX/XLSX parsing, DOCX export, and the
calculator. **The known defects in those modules — the 32-hour "24-hour" window,
the query that reduces "U.S. tariffs" to "U", the calculator hang — are still
present there.** They are fixed as each is ported, not patched in place.

## Data

There is no data migration. The prototype never persisted anything: its only
store was `st.session_state`, destroyed on refresh. Conversations exported to
JSON from the prototype cannot be imported into the new app yet.

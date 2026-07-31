# Security

Scope: the rebuilt application in `apps/`. The preserved prototype in
`legacy_streamlit/` is **not** hardened — its findings are recorded in
[AUDIT.md](AUDIT.md) §6 and it should be treated as local-only, single-user code.

## Secrets

- One secret is required: `TOKENROUTER_API_KEY`.
- `.env` is gitignored. **It has never been committed** — verified by listing
  every path in every commit (`git log --all --name-only`) and by scanning every
  blob reachable from every ref for key-shaped strings. Both returned nothing.
- `.gitignore` was widened during the rebuild. It previously allowed `.env.local`,
  `.env.production`, a root `secrets.toml`, `venv/`, `env/`, local databases, and
  the app's own conversation exports to be committed.
- The key is held as a Pydantic `SecretStr`, so accidental interpolation into a
  log line or an error renders `**********`.
- CI fails the build if a secrets file becomes tracked or a `sk-…` string appears
  in the tree.

## What is never logged

`logging.py` provides `redact_mapping()` (drops `authorization`, `cookie`,
`api_key`, `token`, `password`, … by key) and `redact_url()` (strips userinfo and
**the entire query string**, because query strings routinely carry signed-URL
tokens and session ids).

The request middleware logs method, path, status, and duration. It does not log
query strings, headers, or bodies. Message content is never logged.

## Error handling

Raw exception text never reaches the client. The prototype rendered provider
exceptions into the chat transcript, persisted them, re-sent them to the model as
context, and wrote them into exports — leaking internal hostnames and the
server's absolute home path.

Here:

- Every deliberate failure is a `KimiError` with a stable `code` and a
  user-facing message written by hand.
- The catch-all handler logs `type(exc).__name__` only and returns a generic
  message. Exception detail is attached **only** when `DEBUG=true`.
- Covered by `test_raw_exception_text_is_never_sent_to_the_client`, which raises
  an exception containing a fake DSN with a password and asserts neither the
  credential nor the host appears anywhere in the response.

## Prompt injection

Web pages, documents, and tool output are untrusted input. The system prompt
states this explicitly and instructs the model never to follow instructions found
inside such content, and never to let it override the rules. Asserted by
`test_system_prompt_marks_tool_content_as_untrusted`.

This is a mitigation, not a guarantee. When tools land in Phase 5, untrusted
content must additionally be fenced and structurally separated from instructions
— the prototype interpolated up to 20,000 characters of page text directly
alongside its own framing with no delimiter the page could not forge.

## Injection and traversal

- **SQL:** all queries go through SQLAlchemy with bound parameters. Search uses
  `ilike` with a bound pattern. `test_title_search_uses_bound_parameter` sends
  `'; DROP TABLE messages;--` as a query and asserts the table survives.
- **Code execution:** no `eval`, `exec`, `subprocess`, `shell=True`, `pickle`, or
  `yaml.load` anywhere in `apps/api`.
- **Path traversal:** the rebuilt API does not yet accept file uploads and writes
  no user-controlled paths.
- **XSS:** React escapes by default and the app uses no `dangerouslySetInnerHTML`
  except for the pre-paint theme script, which is a compile-time constant string.

## Transport and access

- The API binds `127.0.0.1` by default.
- CORS is an explicit allowlist, defaulting to `http://localhost:3000`.
- There is **no authentication**. The app is single-user and local. Do not expose
  this port to a network without putting auth in front of it.

## SSRF

Not applicable to `apps/api` today: it makes exactly one outbound request, to the
configured provider base URL. It becomes critical in Phase 5. The prototype's
`is_public_web_url` policy is worth porting, but the audit found it is
TOCTOU-vulnerable — it resolves DNS then discards the addresses, and httpx
re-resolves at connect time — so the port must pin the resolved address, not just
validate the hostname.

## Dependencies

Python dependencies are pinned by `apps/api/uv.lock`; JavaScript by
`apps/web/package-lock.json`. The prototype had 15 lower-bound-only requirements
and no lockfile while monkeypatching `openai` internals.

## Known gaps

1. No authentication or rate limiting.
2. No automated dependency vulnerability scan in CI yet.
3. `legacy_streamlit/` retains every finding in AUDIT.md §5–6, including a
   calculator that can hang the process and a browser agent with English-only
   safety denylists.
4. No threat model document yet — deferred until the tool and browser layers
   exist, since they are what create the interesting attack surface.

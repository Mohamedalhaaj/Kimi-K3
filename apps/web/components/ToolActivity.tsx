"use client";

import { useState } from "react";
import type { ToolInvocation, ToolStatus } from "@/lib/types";
import { AlertIcon, CheckIcon, ChevronIcon, SearchIcon, StopIcon } from "./icons";

const TOOL_NAMES: Record<string, string> = {
  calculator: "Calculator",
  open_public_url: "Opening page",
  read_article: "Reading article",
  web_search: "Searching the web",
  news_search: "Searching news",
};

/**
 * Every state in the brief has a distinct, honest presentation. In particular
 * "completed" is only ever shown once the tool has actually finished — the
 * prototype reported success for browser actions that never ran.
 */
const STATE: Record<
  ToolStatus,
  { label: string; tone: "busy" | "ok" | "warn" | "bad" | "muted" }
> = {
  queued: { label: "Queued", tone: "muted" },
  running: { label: "Running", tone: "busy" },
  completed: { label: "Completed", tone: "ok" },
  completed_with_warnings: { label: "Completed with warnings", tone: "warn" },
  failed: { label: "Failed", tone: "bad" },
  cancelled: { label: "Cancelled", tone: "muted" },
  waiting_for_approval: { label: "Waiting for approval", tone: "warn" },
};

const TONE_CLASS = {
  busy: "text-accent-fg",
  ok: "text-fg-muted",
  warn: "text-warn-fg",
  bad: "text-danger",
  muted: "text-fg-subtle",
} as const;

function Dots() {
  return (
    <span className="flex gap-0.5" aria-hidden>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-1 rounded-full bg-current"
          style={{
            animation: "caret-blink 1.2s ease-in-out infinite",
            animationDelay: `${i * 160}ms`,
          }}
        />
      ))}
    </span>
  );
}

export function ToolActivity({ tool }: { tool: ToolInvocation }) {
  const [open, setOpen] = useState(false);
  const state = STATE[tool.status] ?? STATE.queued;
  const name = TOOL_NAMES[tool.tool_id] ?? tool.tool_id;
  const running = tool.status === "running" || tool.status === "queued";

  const query =
    (tool.arguments?.query as string | undefined) ??
    (tool.arguments?.expression as string | undefined) ??
    (tool.arguments?.url as string | undefined) ??
    "";

  return (
    <div className="measure mb-3 rounded-panel border border-border bg-bg-sunken text-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-start"
      >
        <span className={TONE_CLASS[state.tone]}>
          {running ? (
            <Dots />
          ) : state.tone === "bad" ? (
            <AlertIcon className="size-3.5" />
          ) : state.tone === "warn" ? (
            <AlertIcon className="size-3.5" />
          ) : state.tone === "muted" ? (
            <StopIcon className="size-3" />
          ) : (
            <CheckIcon className="size-3.5" />
          )}
        </span>

        <SearchIcon className="size-3.5 shrink-0 text-fg-subtle" />
        <span className="min-w-0 flex-1 truncate text-fg">
          {name}
          {query && <span className="text-fg-muted"> · {query}</span>}
        </span>

        <span className={`shrink-0 text-2xs ${TONE_CLASS[state.tone]}`}>
          {state.label}
        </span>

        {/* Tool time, always separate from model time. */}
        {typeof tool.duration_ms === "number" && !running && (
          <span className="shrink-0 text-2xs text-fg-subtle" dir="ltr">
            {(tool.duration_ms / 1000).toFixed(1)}s
          </span>
        )}

        <ChevronIcon
          className={`size-3.5 shrink-0 text-fg-subtle transition-transform ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>

      {open && (
        <div className="border-t border-border px-3 py-2 text-2xs text-fg-muted">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <dt className="text-fg-subtle">Tool</dt>
            <dd dir="ltr">{tool.tool_id}</dd>
            {tool.reason && (
              <>
                <dt className="text-fg-subtle">Chosen because</dt>
                <dd>{tool.reason}</dd>
              </>
            )}
            {typeof tool.duration_ms === "number" && (
              <>
                <dt className="text-fg-subtle">Tool time</dt>
                <dd dir="ltr">{tool.duration_ms.toFixed(0)} ms</dd>
              </>
            )}
            {tool.result?.counts != null && (
              <>
                <dt className="text-fg-subtle">Results</dt>
                <dd dir="ltr">
                  {JSON.stringify(tool.result.counts as Record<string, number>)}
                </dd>
              </>
            )}
          </dl>

          {!!tool.warnings?.length && (
            <ul className="mt-2 space-y-1">
              {tool.warnings.map((w, i) => (
                <li key={i} className="flex gap-1.5 text-warn-fg">
                  <AlertIcon className="mt-0.5 size-3 shrink-0" />
                  <span>{w.message}</span>
                </li>
              ))}
            </ul>
          )}

          {tool.error && (
            <p className="mt-2 text-danger">{tool.error.message}</p>
          )}
        </div>
      )}
    </div>
  );
}

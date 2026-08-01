"use client";

import { useState } from "react";
import type { Citation } from "@/lib/types";
import { detectDirection } from "@/lib/direction";
import { AlertIcon, ChevronIcon } from "./icons";

/** Colour is used only to separate "we read it" from "we could not". */
const STATUS_TONE: Record<string, string> = {
  full: "text-fg-muted",
  partial: "text-warn-fg",
  snippet_only: "text-warn-fg",
  paywalled: "text-warn-fg",
  blocked: "text-danger",
  failed: "text-danger",
};

function formatDate(iso: string | null, verified: boolean): string {
  if (!iso) return "Date unknown";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Date unknown";
  const text = date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  return verified ? text : `${text} · Date unverified`;
}

function SourceRow({ source }: { source: Citation }) {
  return (
    <li className="border-t border-border py-3 first:border-t-0 first:pt-0">
      <div className="flex gap-2.5">
        <span
          className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded bg-accent-soft text-2xs font-medium text-accent-fg"
          aria-hidden
        >
          {source.index}
        </span>

        <div className="min-w-0 flex-1">
          <a
            href={source.url}
            target="_blank"
            rel="noopener noreferrer nofollow"
            dir={detectDirection(source.title)}
            className="block text-sm font-medium text-fg underline-offset-2 hover:underline"
          >
            {source.title || source.url}
          </a>

          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-fg-muted">
            {source.publisher && <span dir="ltr">{source.publisher}</span>}
            <span aria-hidden>·</span>
            <span dir="ltr">{formatDate(source.published_at, source.date_verified)}</span>
            <span aria-hidden>·</span>
            {/* The honest extraction label, never implied. */}
            <span className={STATUS_TONE[source.status] ?? "text-fg-muted"}>
              {source.status_label}
            </span>
          </p>

          <p className="mt-1 text-2xs text-fg-subtle" dir="ltr">
            via {source.provider} · {source.retrieval.replace("_", " ")}
            {source.aggregator_url && " · resolved from aggregator"}
          </p>

          {source.excerpt && (
            <p
              dir={detectDirection(source.excerpt)}
              className="mt-1.5 line-clamp-3 text-xs leading-relaxed text-fg-muted"
            >
              {source.excerpt}
            </p>
          )}

          {source.note && (
            <p className="mt-1 flex items-start gap-1 text-2xs text-warn-fg">
              <AlertIcon className="mt-0.5 size-3 shrink-0" />
              {source.note}
            </p>
          )}
        </div>
      </div>
    </li>
  );
}

export function Sources({ sources }: { sources: Citation[] }) {
  const [open, setOpen] = useState(true);
  if (!sources.length) return null;

  const readable = sources.filter((s) => s.status === "full" || s.status === "partial").length;

  return (
    <section className="measure mt-4 rounded-panel border border-border bg-bg-sunken">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-start text-sm"
      >
        <span className="flex-1 font-medium text-fg">
          Sources
          <span className="ms-1.5 font-normal text-fg-muted">
            {sources.length} · {readable} read in full or part
          </span>
        </span>
        <ChevronIcon
          className={`size-4 text-fg-subtle transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <ul className="px-3 pb-3">
          {sources.map((s) => (
            <SourceRow key={`${s.index}-${s.url}`} source={s} />
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * Renders assistant text with `[n]` turned into a link to source n.
 *
 * A bracketed number with no matching source is left as plain text rather than
 * linked — the app must never present a citation it cannot resolve.
 */
export function CitedText({
  text,
  sources,
  className,
  dir,
}: {
  text: string;
  sources: Citation[];
  className?: string;
  dir?: "ltr" | "rtl";
}) {
  if (!sources.length || !text.includes("[")) {
    return (
      <div dir={dir} className={className}>
        {text}
      </div>
    );
  }

  const byIndex = new Map(sources.map((s) => [s.index, s]));
  const parts: React.ReactNode[] = [];
  const pattern = /\[(\d{1,2})\]/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = pattern.exec(text)) !== null) {
    const source = byIndex.get(Number(match[1]));
    if (match.index > last) parts.push(text.slice(last, match.index));
    if (source) {
      parts.push(
        <a
          key={`c${key++}`}
          href={source.url}
          target="_blank"
          rel="noopener noreferrer nofollow"
          title={`${source.title} — ${source.publisher}`}
          className="mx-0.5 inline-flex min-w-4 items-center justify-center rounded bg-accent-soft px-1 align-baseline text-2xs font-medium text-accent-fg no-underline hover:bg-accent hover:text-white"
        >
          {source.index}
        </a>,
      );
    } else {
      parts.push(match[0]);
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));

  return (
    <div dir={dir} className={className}>
      {parts}
    </div>
  );
}

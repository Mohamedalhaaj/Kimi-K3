"use client";

import { useState } from "react";
import type { Message } from "@/lib/types";
import { detectDirection } from "@/lib/direction";
import { AlertIcon, CheckIcon, CopyIcon, RefreshIcon } from "./icons";
import { CitedText, Sources } from "./Sources";
import { ToolActivity } from "./ToolActivity";

function Meta({ message }: { message: Message }) {
  const bits: string[] = [];
  const ttft = message.timing?.first_token_ms;
  const total = message.timing?.total_ms;
  const toolMs = message.timing?.tool_ms;

  // Only ever render a number that was actually measured, and keep tool time
  // distinct from model time — a deterministic answer is never "0.0s".
  if (typeof toolMs === "number") bits.push(`${(toolMs / 1000).toFixed(1)}s tool`);
  if (typeof ttft === "number") bits.push(`${(ttft / 1000).toFixed(1)}s to first token`);
  if (typeof total === "number") bits.push(`${(total / 1000).toFixed(1)}s total`);
  if (message.usage?.total_tokens) bits.push(`${message.usage.total_tokens} tokens`);
  if (message.model_id) bits.push(message.model_id);
  else if (message.tool) bits.push("no model call");

  if (!bits.length) return null;
  return (
    <p className="mt-2 text-2xs text-fg-subtle" dir="ltr">
      {bits.join(" · ")}
    </p>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1600);
        } catch {
          /* clipboard blocked — the button simply does not confirm */
        }
      }}
      className="inline-flex items-center gap-1.5 rounded-control px-2 py-1 text-2xs text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg"
    >
      {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

interface Props {
  message: Message;
  isStreaming?: boolean;
  onRegenerate?: () => void;
}

export function MessageView({ message, isStreaming, onRegenerate }: Props) {
  const dir = detectDirection(message.content);
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <article className="flex justify-end" aria-label="Your message">
        <div
          dir={dir}
          className="measure whitespace-pre-wrap break-words rounded-panel bg-accent-soft px-4 py-2.5 text-md leading-relaxed text-fg"
        >
          {message.content}
        </div>
      </article>
    );
  }

  const hasText = message.content.length > 0;
  const err = message.error;

  const citations = message.citations ?? [];

  return (
    <article className="animate-message-in" aria-label="Assistant message">
      {message.tool && <ToolActivity tool={message.tool} />}

      {hasText && (
        <CitedText
          text={message.content}
          sources={citations}
          dir={dir}
          className={`measure whitespace-pre-wrap break-words text-md leading-relaxed text-fg ${
            isStreaming ? "streaming-caret" : ""
          }`}
        />
      )}

      {/* An error never replaces the text the user already watched arrive;
          it is shown beneath whatever was received. */}
      {err && (
        <div
          role="alert"
          className={`measure mt-3 flex gap-2.5 rounded-panel px-3 py-2.5 text-sm ${
            err.code === "cancelled"
              ? "bg-bg-sunken text-fg-muted"
              : "bg-danger-soft text-danger"
          }`}
        >
          {err.code !== "cancelled" && <AlertIcon className="mt-0.5 size-4 shrink-0" />}
          <div>
            <p>{err.message}</p>
            {hasText && err.code !== "cancelled" && (
              <p className="mt-1 text-fg-muted">
                The partial answer above was kept.
              </p>
            )}
            {err.retryable && onRegenerate && (
              <button
                type="button"
                onClick={onRegenerate}
                className="mt-2 inline-flex items-center gap-1.5 rounded-control border border-border px-2 py-1 text-2xs text-fg transition-colors hover:bg-bg-hover"
              >
                <RefreshIcon className="size-3.5" />
                Try again
              </button>
            )}
          </div>
        </div>
      )}

      {!isStreaming && hasText && (
        <div className="mt-1 flex items-center gap-1">
          <CopyButton text={message.content} />
          {onRegenerate && (
            <button
              type="button"
              onClick={onRegenerate}
              className="inline-flex items-center gap-1.5 rounded-control px-2 py-1 text-2xs text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg"
            >
              <RefreshIcon className="size-3.5" />
              Regenerate
            </button>
          )}
        </div>
      )}

      {!isStreaming && citations.length > 0 && <Sources sources={citations} />}

      {!isStreaming && <Meta message={message} />}
    </article>
  );
}

/**
 * Shown between "sent" and the first token.
 *
 * This is deliberately *not* an empty assistant bubble: it names what is
 * happening and shows a live elapsed clock, because the free model's time to
 * first token is measured in seconds and silence reads as a hang.
 */
export function PendingIndicator({ elapsedMs }: { elapsedMs: number }) {
  return (
    <div
      className="flex items-center gap-2.5 text-sm text-fg-muted"
      role="status"
      aria-live="polite"
    >
      <span className="flex gap-1" aria-hidden>
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="size-1.5 rounded-full bg-fg-subtle"
            style={{
              animation: "caret-blink 1.2s ease-in-out infinite",
              animationDelay: `${i * 160}ms`,
            }}
          />
        ))}
      </span>
      <span>
        Waiting for the model
        {elapsedMs > 1500 && (
          <span className="text-fg-subtle" dir="ltr">
            {" "}
            · {(elapsedMs / 1000).toFixed(0)}s
          </span>
        )}
      </span>
    </div>
  );
}

"use client";

import { useEffect, useRef } from "react";
import { SendIcon, StopIcon } from "./icons";
import { detectDirection } from "@/lib/direction";

const MAX_ROWS_PX = 216; // ~9 rows before the textarea starts scrolling

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  streaming: boolean;
  disabled?: boolean;
}

export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  streaming,
  disabled,
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Grow with content, then scroll. Measured from scrollHeight after a reset
  // so the box shrinks again when text is deleted.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_ROWS_PX)}px`;
    el.style.overflowY = el.scrollHeight > MAX_ROWS_PX ? "auto" : "hidden";
  }, [value]);

  const canSend = value.trim().length > 0 && !streaming && !disabled;

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends; Shift+Enter is a newline. IME composition must never send.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (canSend) onSubmit();
    }
  }

  const dir = detectDirection(value);

  return (
    <div className="border-t border-border bg-bg px-4 pb-4 pt-3 sm:px-6">
      <div className="measure mx-auto">
        <div className="flex items-end gap-2 rounded-panel border border-border bg-bg-raised p-2 shadow-sm transition-colors focus-within:border-accent-fg">
          <label htmlFor="composer" className="sr-only">
            Message
          </label>
          <textarea
            id="composer"
            ref={ref}
            dir={dir}
            rows={1}
            value={value}
            disabled={disabled}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask anything, or paste a link to read…"
            aria-describedby="composer-hint"
            className="max-h-[216px] flex-1 resize-none bg-transparent px-2 py-1.5 text-md leading-relaxed text-fg outline-none disabled:opacity-60"
          />

          {streaming ? (
            <button
              type="button"
              onClick={onStop}
              aria-label="Stop generating"
              className="flex size-9 shrink-0 items-center justify-center rounded-control border border-border text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg"
            >
              <StopIcon className="size-4" />
            </button>
          ) : (
            <button
              type="button"
              onClick={onSubmit}
              disabled={!canSend}
              aria-label="Send message"
              className="flex size-9 shrink-0 items-center justify-center rounded-control bg-accent text-white transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-border-strong disabled:text-fg-subtle"
            >
              <SendIcon className="size-4" />
            </button>
          )}
        </div>

        <p id="composer-hint" className="mt-2 px-1 text-2xs text-fg-subtle">
          Enter to send · Shift+Enter for a new line
        </p>
      </div>
    </div>
  );
}

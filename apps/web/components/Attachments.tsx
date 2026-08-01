"use client";

import type { AttachedFile, ParseStatus } from "@/lib/types";
import { formatBytes } from "@/lib/images";
import { detectDirection } from "@/lib/direction";
import { AlertIcon, CheckIcon, TrashIcon } from "./icons";

/**
 * Every status is shown for what it is. A scanned PDF says "Scanned — no text
 * layer", not nothing: the prototype dropped such files silently while still
 * displaying a paperclip, so the user believed the file had been read.
 */
const STATUS: Record<ParseStatus, { label: string; tone: "ok" | "warn" | "bad" }> = {
  parsed: { label: "Read", tone: "ok" },
  partial: { label: "Read in part", tone: "warn" },
  no_text_layer: { label: "Scanned — no text", tone: "warn" },
  encrypted: { label: "Encrypted", tone: "bad" },
  password_required: { label: "Password required", tone: "bad" },
  unsupported: { label: "Unsupported type", tone: "bad" },
  too_large: { label: "Too large", tone: "bad" },
  failed: { label: "Could not be read", tone: "bad" },
};

const TONE = {
  ok: "text-fg-muted",
  warn: "text-warn-fg",
  bad: "text-danger",
} as const;

const KIND_LABEL: Record<string, string> = {
  pdf: "PDF",
  docx: "Word",
  pptx: "Slides",
  xlsx: "Sheet",
  csv: "CSV",
  text: "Text",
  image: "Image",
  unknown: "File",
};

function FileChip({
  file,
  onRemove,
}: {
  file: AttachedFile;
  onRemove?: () => void;
}) {
  const state = STATUS[file.status] ?? STATUS.failed;
  const readable = file.status === "parsed" || file.status === "partial";

  return (
    <li className="flex max-w-full items-start gap-2 rounded-control border border-border bg-bg-raised px-2.5 py-1.5">
      <span
        className={`mt-0.5 shrink-0 ${TONE[state.tone]}`}
        aria-hidden
      >
        {readable ? <CheckIcon className="size-3.5" /> : <AlertIcon className="size-3.5" />}
      </span>

      <span className="min-w-0 flex-1">
        <span
          dir={detectDirection(file.filename)}
          className="block truncate text-xs text-fg"
          title={file.summary}
        >
          {file.filename}
        </span>
        <span className="mt-0.5 block text-2xs text-fg-subtle" dir="ltr">
          {KIND_LABEL[file.kind] ?? "File"} · {formatBytes(file.size_bytes)} ·{" "}
          <span className={TONE[state.tone]}>{state.label}</span>
        </span>
      </span>

      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${file.filename}`}
          className="mt-0.5 shrink-0 text-fg-subtle transition-colors hover:text-danger"
        >
          <TrashIcon className="size-3.5" />
        </button>
      )}
    </li>
  );
}

export function AttachmentTray({
  files,
  onRemove,
  uploading,
}: {
  files: AttachedFile[];
  onRemove?: (id: string) => void;
  uploading?: number;
}) {
  if (!files.length && !uploading) return null;

  const unreadable = files.filter(
    (f) => f.status !== "parsed" && f.status !== "partial",
  );

  return (
    <div className="mb-2">
      <ul className="flex flex-wrap gap-2" aria-label="Attached files">
        {files.map((f) => (
          <FileChip key={f.id} file={f} onRemove={onRemove && (() => onRemove(f.id))} />
        ))}
        {!!uploading && (
          <li className="skeleton h-11 w-40 rounded-control" aria-label="Reading file" />
        )}
      </ul>

      {unreadable.length > 0 && (
        <p role="status" className="mt-1.5 px-1 text-2xs text-warn-fg">
          {unreadable.length === 1
            ? `${unreadable[0].filename}: ${unreadable[0].summary}`
            : `${unreadable.length} file(s) could not be read in full. They are still listed for the model, which will say so rather than guess.`}
        </p>
      )}
    </div>
  );
}

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PaperclipIcon, SendIcon, StopIcon, TrashIcon } from "./icons";
import { detectDirection } from "@/lib/direction";
import {
  MAX_IMAGES,
  type PreparedImage,
  formatBytes,
  isSupportedImage,
  prepareImage,
} from "@/lib/images";
import type { AttachedFile } from "@/lib/types";
import { AttachmentTray } from "./Attachments";

const MAX_ROWS_PX = 216; // ~9 rows before the textarea starts scrolling

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  streaming: boolean;
  disabled?: boolean;
  images: PreparedImage[];
  onImagesChange: (images: PreparedImage[]) => void;
  /** False when the selected model has no vision capability. */
  visionSupported: boolean;
  onImageError: (message: string) => void;
  files: AttachedFile[];
  uploading: number;
  onFiles: (files: File[]) => void;
  onRemoveFile: (id: string) => void;
}

export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  streaming,
  disabled,
  images,
  onImagesChange,
  visionSupported,
  onImageError,
  files,
  uploading,
  onFiles,
  onRemoveFile,
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  // Grow with content, then scroll. Measured from scrollHeight after a reset
  // so the box shrinks again when text is deleted.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_ROWS_PX)}px`;
    el.style.overflowY = el.scrollHeight > MAX_ROWS_PX ? "auto" : "hidden";
  }, [value]);

  const addFiles = useCallback(
    async (incoming: File[]) => {
      const usable = incoming.filter(isSupportedImage);
      const documents = incoming.filter((f) => !isSupportedImage(f));
      // Documents go to the server-side parser; images stay inline.
      if (documents.length) onFiles(documents);
      if (!usable.length) return;

      const room = MAX_IMAGES - images.length;
      if (room <= 0) {
        onImageError(`You can attach up to ${MAX_IMAGES} images.`);
        return;
      }
      if (usable.length > room) {
        onImageError(`Only the first ${room} image(s) were attached.`);
      }

      const prepared: PreparedImage[] = [];
      for (const file of usable.slice(0, room)) {
        try {
          prepared.push(await prepareImage(file));
        } catch (err) {
          onImageError(err instanceof Error ? err.message : "Could not read that image.");
        }
      }
      if (prepared.length) onImagesChange([...images, ...prepared]);
    },
    [images, onImagesChange, onImageError, onFiles],
  );

  const canSend =
    (value.trim().length > 0 || images.length > 0 || files.length > 0) &&
    !streaming &&
    !disabled &&
    uploading === 0;

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends; Shift+Enter is a newline. IME composition must never send.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (canSend) onSubmit();
    }
  }

  const dir = detectDirection(value);

  return (
    <div
      className="border-t border-border bg-bg px-4 pb-4 pt-3 sm:px-6"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        if (!e.dataTransfer.files.length) return;
        e.preventDefault();
        setDragging(false);
        void addFiles(Array.from(e.dataTransfer.files));
      }}
    >
      <div className="measure mx-auto">
        <AttachmentTray files={files} onRemove={onRemoveFile} uploading={uploading} />

        {images.length > 0 && (
          <ul className="mb-2 flex flex-wrap gap-2" aria-label="Attached images">
            {images.map((img) => (
              <li
                key={img.id}
                className="group relative overflow-hidden rounded-control border border-border"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={img.dataUrl}
                  alt={img.name}
                  className="size-16 object-cover"
                />
                <button
                  type="button"
                  onClick={() => onImagesChange(images.filter((i) => i.id !== img.id))}
                  aria-label={`Remove ${img.name}`}
                  className="absolute end-0.5 top-0.5 flex size-5 items-center justify-center rounded bg-bg/85 text-fg-muted opacity-0 transition-opacity hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
                >
                  <TrashIcon className="size-3" />
                </button>
                <span className="absolute inset-x-0 bottom-0 bg-bg/85 px-1 text-center text-[10px] text-fg-muted">
                  {formatBytes(img.bytes)}
                </span>
              </li>
            ))}
          </ul>
        )}

        {images.length > 0 && !visionSupported && (
          <p role="status" className="mb-2 px-1 text-2xs text-warn-fg">
            The selected model cannot read images. They will not be sent — switch
            to a vision-capable model first.
          </p>
        )}

        <div
          className={`flex items-end gap-2 rounded-panel border bg-bg-raised p-2 shadow-sm transition-colors focus-within:border-accent-fg ${
            dragging ? "border-accent-fg bg-accent-soft" : "border-border"
          }`}
        >
          <input
            ref={fileRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif,image/avif,.pdf,.docx,.pptx,.xlsx,.xlsm,.csv,.txt,.md,.json,.yaml,.yml,.xml,.html,.py,.js,.ts,.srt,.vtt"
            multiple
            className="sr-only"
            onChange={(e) => {
              void addFiles(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={disabled}
            aria-label="Attach a file"
            title="Attach a PDF, Word, PowerPoint, Excel, CSV, text file or image"
            className="flex size-9 shrink-0 items-center justify-center rounded-control text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg disabled:cursor-not-allowed disabled:opacity-40"
          >
            <PaperclipIcon className="size-4" />
          </button>

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
            onPaste={(e) => {
              const files = Array.from(e.clipboardData.files);
              if (files.length) {
                e.preventDefault();
                void addFiles(files);
              }
            }}
            placeholder="Ask anything, paste a link, or attach a document…"
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
          Enter to send · Shift+Enter for a new line · PDF, Word, PowerPoint, Excel, CSV, text and images
        </p>
      </div>
    </div>
  );
}

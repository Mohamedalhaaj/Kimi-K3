"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiRequestError, streamChat, uploadFiles } from "@/lib/api";
import type {
  AttachedFile,
  ChatMode,
  Citation,
  Conversation,
  Message,
  ModelInfo,
  ResearchMode,
} from "@/lib/types";
import { useTheme } from "@/lib/useTheme";
import type { PreparedImage } from "@/lib/images";
import { Composer } from "./Composer";
import { MessageView, PendingIndicator } from "./MessageView";
import { Sidebar } from "./Sidebar";
import { AlertIcon, MoonIcon, SidebarIcon, SunIcon } from "./icons";

const MODES: { id: ChatMode; label: string; hint: string }[] = [
  { id: "fast", label: "Fast", hint: "Short answers, least context" },
  { id: "balanced", label: "Balanced", hint: "Default" },
  { id: "deep", label: "Deep", hint: "Longest answers, most context" },
];

/** A local-only placeholder row while the assistant turn is in flight. */
function draftMessage(conversationId: string, seq: number): Message {
  return {
    id: `draft-${seq}`,
    conversation_id: conversationId,
    seq,
    role: "assistant",
    content: "",
    model_id: null,
    usage: null,
    timing: null,
    error: null,
    tool: null,
    citations: null,
    created_at: new Date().toISOString(),
  };
}

export function Workspace() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [banner, setBanner] = useState<string | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelId, setModelId] = useState<string>("");
  const [mode, setMode] = useState<ChatMode>("balanced");
  const [research, setResearch] = useState<ResearchMode>("auto");
  const [images, setImages] = useState<PreparedImage[]>([]);
  const [files, setFiles] = useState<AttachedFile[]>([]);
  const [uploading, setUploading] = useState(0);
  const [theme, applyTheme] = useTheme();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const streamStartRef = useRef(0);

  // ---- data --------------------------------------------------------
  const refreshList = useCallback(async (q: string) => {
    try {
      const res = await api.listConversations(q || undefined);
      setConversations(res.items);
      setBanner(null);
    } catch (e) {
      setBanner(
        e instanceof ApiRequestError ? e.message : "Could not load conversations.",
      );
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => void refreshList(query), query ? 200 : 0);
    return () => clearTimeout(t);
  }, [query, refreshList]);

  useEffect(() => {
    api
      .models()
      .then((m) => {
        setModels(m.models);
        setModelId((cur) => cur || m.default);
      })
      .catch(() => {
        /* the picker stays empty; chat still uses the server default */
      });
  }, []);

  /** Files are scoped to a conversation, so one must exist before uploading. */
  const ensureConversation = useCallback(async (): Promise<string | null> => {
    if (activeId) return activeId;
    try {
      const convo = await api.createConversation({ mode });
      setActiveId(convo.id);
      setConversations((c) => [convo, ...c]);
      return convo.id;
    } catch (e) {
      setBanner(
        e instanceof ApiRequestError ? e.message : "Could not start a conversation.",
      );
      return null;
    }
  }, [activeId, mode]);

  const onFiles = useCallback(
    async (incoming: File[]) => {
      const cid = await ensureConversation();
      if (!cid) return;
      setUploading((n) => n + incoming.length);
      try {
        const parsed = await uploadFiles(cid, incoming);
        setFiles((f) => [...f, ...parsed]);
        // Surface anything unreadable immediately rather than at send time.
        const bad = parsed.filter(
          (p) => p.status !== "parsed" && p.status !== "partial",
        );
        if (bad.length) setBanner(bad[0].summary);
      } catch (e) {
        setBanner(e instanceof ApiRequestError ? e.message : "Upload failed.");
      } finally {
        setUploading((n) => Math.max(0, n - incoming.length));
      }
    },
    [ensureConversation],
  );

  const onRemoveFile = useCallback((id: string) => {
    setFiles((f) => f.filter((x) => x.id !== id));
    void api.deleteFile(id).catch(() => {});
  }, []);

  const openConversation = useCallback(async (id: string) => {
    setActiveId(id);
    setSidebarOpen(false);
    try {
      const detail = await api.getConversation(id);
      setMessages(detail.messages);
      setFiles([]);
      void api
        .listFiles(id)
        .then((r) => setFiles(r.files))
        .catch(() => {});
      if (detail.model_id) setModelId(detail.model_id);
      if (detail.mode) setMode(detail.mode as ChatMode);
      stickToBottom.current = true;
    } catch (e) {
      setBanner(
        e instanceof ApiRequestError ? e.message : "Could not open that conversation.",
      );
    }
  }, []);

  // ---- scrolling ---------------------------------------------------
  // Only auto-scroll while the user is already at the bottom, so reading
  // back through a long answer is never yanked away mid-sentence.
  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottom.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }, []);

  useEffect(() => {
    if (!stickToBottom.current) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // ---- elapsed clock while waiting for the first token -------------
  // The start stamp is written in the send handler, so this effect only
  // schedules ticks and never sets state synchronously on mount.
  useEffect(() => {
    if (!streaming) return;
    const t = setInterval(
      () => setElapsed(performance.now() - streamStartRef.current),
      250,
    );
    return () => clearInterval(t);
  }, [streaming]);

  // ---- send --------------------------------------------------------
  const send = useCallback(
    async (
      text: string,
      conversationId: string,
      attach: PreparedImage[] = [],
      documentIds: string[] = [],
    ) => {
      const controller = new AbortController();
      abortRef.current = controller;
      streamStartRef.current = performance.now();
      setElapsed(0);
      setStreaming(true);
      stickToBottom.current = true;

      const baseSeq = messages.length;
      const userMsg: Message = {
        ...draftMessage(conversationId, baseSeq),
        id: `local-${baseSeq}`,
        role: "user",
        content: text,
      };
      const assistantSeq = baseSeq + 1;
      setMessages((m) => [...m, userMsg, draftMessage(conversationId, assistantSeq)]);

      const patchAssistant = (patch: Partial<Message>) =>
        setMessages((m) =>
          m.map((msg) =>
            msg.seq === assistantSeq && msg.role === "assistant"
              ? { ...msg, ...patch }
              : msg,
          ),
        );

      try {
        for await (const ev of streamChat(
          {
            conversation_id: conversationId,
            content: text,
            model_id: modelId,
            mode,
            research,
            images: attach.map((i) => ({ data_url: i.dataUrl })),
            document_ids: documentIds,
          },
          controller.signal,
        )) {
          if (ev.type === "start") {
            setConversations((cs) =>
              cs.map((c) => (c.id === conversationId ? { ...c, title: ev.title } : c)),
            );
            patchAssistant({ model_id: ev.model_id });
          } else if (ev.type === "delta") {
            setMessages((m) =>
              m.map((msg) =>
                msg.seq === assistantSeq && msg.role === "assistant"
                  ? { ...msg, content: msg.content + ev.text }
                  : msg,
              ),
            );
          } else if (ev.type === "tool") {
            // The tool card updates in place through its lifecycle, so the
            // user always sees the real state rather than a stuck "running".
            patchAssistant({
              tool: {
                tool_id: ev.tool_id,
                status: ev.status,
                arguments: ev.arguments,
                result: ev.result,
                warnings: ev.warnings,
                error: ev.error,
                duration_ms: ev.duration_ms,
                renderer: ev.renderer,
                reason: ev.reason,
              },
            });
          } else if (ev.type === "sources") {
            patchAssistant({ citations: ev.sources as Citation[] });
          } else if (ev.type === "context") {
            /* reported for the diagnostics panel; not surfaced inline */
          } else if (ev.type === "warning") {
            setBanner(ev.message);
          } else if (ev.type === "error") {
            patchAssistant({
              error: { code: ev.code, message: ev.message, retryable: ev.retryable },
            });
          } else if (ev.type === "done") {
            patchAssistant({ usage: ev.usage, timing: ev.timing });
            // A tool-only turn produces no text; make sure it never leaves an
            // empty card by falling back to the tool's own summary.
          }
        }
      } catch (e) {
        patchAssistant({
          error: {
            code: "network",
            message:
              e instanceof ApiRequestError ? e.message : "The response was interrupted.",
            retryable: true,
          },
        });
      } finally {
        setStreaming(false);
        abortRef.current = null;
        void refreshList(query);
      }
    },
    [messages.length, modelId, mode, research, query, refreshList],
  );

  const onSubmit = useCallback(async () => {
    const text = draft.trim();
    if ((!text && images.length === 0 && files.length === 0) || streaming) return;

    const id = await ensureConversation();
    if (!id) return;

    const attach = images;
    const docIds = files.map((f) => f.id);
    setDraft("");
    setImages([]);
    setFiles([]);
    await send(text, id, attach, docIds);
  }, [draft, images, files, streaming, ensureConversation, send]);

  const onStop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }, []);

  const onRegenerate = useCallback(async () => {
    if (!activeId || streaming) return;
    // Drop the last assistant turn and resend the user turn that produced it.
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    setMessages((m) => m.slice(0, -2));
    await send(lastUser.content, activeId);
  }, [activeId, streaming, messages, send]);

  const startNew = useCallback(() => {
    onStop();
    setActiveId(null);
    setMessages([]);
    setDraft("");
    setImages([]);
    setFiles([]);
    setSidebarOpen(false);
  }, [onStop]);

  const activeModel = models.find((m) => m.id === modelId);
  const lastAssistantSeq = [...messages]
    .reverse()
    .find((m) => m.role === "assistant")?.seq;
  return (
    <div className="grid h-dvh grid-cols-1 overflow-hidden lg:grid-cols-[280px_minmax(0,1fr)]">
      {/* Sidebar: a permanent rail on desktop, a drawer below lg. */}
      <div className="hidden lg:block">
        <Sidebar
          conversations={conversations}
          activeId={activeId}
          loading={listLoading}
          query={query}
          onQuery={setQuery}
          onSelect={openConversation}
          onNew={startNew}
          onRename={(id, title) => {
            setConversations((c) =>
              c.map((x) => (x.id === id ? { ...x, title } : x)),
            );
            void api.updateConversation(id, { title });
          }}
          onDelete={(id) => {
            setConversations((c) => c.filter((x) => x.id !== id));
            if (id === activeId) startNew();
            void api.deleteConversation(id);
          }}
          onTogglePin={(id, pinned) => {
            setConversations((c) =>
              c.map((x) => (x.id === id ? { ...x, pinned } : x)),
            );
            void api
              .updateConversation(id, { pinned })
              .then(() => refreshList(query));
          }}
        />
      </div>

      {sidebarOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            aria-label="Close menu"
            onClick={() => setSidebarOpen(false)}
            className="absolute inset-0 bg-black/40"
          />
          <div className="absolute inset-y-0 start-0 w-72 shadow-lg">
            <Sidebar
              conversations={conversations}
              activeId={activeId}
              loading={listLoading}
              query={query}
              onQuery={setQuery}
              onSelect={openConversation}
              onNew={startNew}
              onRename={(id, title) => {
                setConversations((c) =>
                  c.map((x) => (x.id === id ? { ...x, title } : x)),
                );
                void api.updateConversation(id, { title });
              }}
              onDelete={(id) => {
                setConversations((c) => c.filter((x) => x.id !== id));
                if (id === activeId) startNew();
                void api.deleteConversation(id);
              }}
              onTogglePin={(id, pinned) => {
                setConversations((c) =>
                  c.map((x) => (x.id === id ? { ...x, pinned } : x)),
                );
                void api.updateConversation(id, { pinned });
              }}
            />
          </div>
        </div>
      )}

      {/*
        The main column is a grid of three rows: header / scroll / composer.
        Because the scroll region is `minmax(0,1fr)` and the composer is `auto`,
        they are siblings and the composer can never overlap the last message —
        no bottom padding to keep in sync with a growing textarea.
      */}
      <main className="grid min-h-0 grid-rows-[auto_minmax(0,1fr)_auto] bg-bg">
        <header className="flex items-center gap-2 border-b border-border px-4 py-2.5 sm:px-6">
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open conversations"
            className="flex size-8 items-center justify-center rounded-control text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg lg:hidden"
          >
            <SidebarIcon className="size-4" />
          </button>

          <div className="flex flex-1 flex-wrap items-center gap-2">
            <label htmlFor="model" className="sr-only">
              Model
            </label>
            <select
              id="model"
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              className="rounded-control border border-border bg-bg-raised px-2 py-1.5 text-xs text-fg outline-none transition-colors hover:bg-bg-hover focus:border-accent-fg"
            >
              {models.length === 0 && <option value="">Loading models…</option>}
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>

            {/* Segmented mode control — three visible options, not a dropdown. */}
            <div
              role="radiogroup"
              aria-label="Response mode"
              className="flex rounded-control border border-border bg-bg-raised p-0.5"
            >
              {MODES.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  role="radio"
                  aria-checked={mode === m.id}
                  title={m.hint}
                  onClick={() => setMode(m.id)}
                  className={`rounded-[6px] px-2.5 py-1 text-xs transition-colors ${
                    mode === m.id
                      ? "bg-accent text-white"
                      : "text-fg-muted hover:text-fg"
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>

            <div
              role="radiogroup"
              aria-label="Web research"
              className="flex rounded-control border border-border bg-bg-raised p-0.5"
            >
              {(
                [
                  { id: "off", label: "No web" },
                  { id: "auto", label: "Auto" },
                  { id: "always", label: "Always" },
                ] as const
              ).map((r) => (
                <button
                  key={r.id}
                  type="button"
                  role="radio"
                  aria-checked={research === r.id}
                  onClick={() => setResearch(r.id)}
                  className={`rounded-[6px] px-2.5 py-1 text-xs transition-colors ${
                    research === r.id
                      ? "bg-accent text-white"
                      : "text-fg-muted hover:text-fg"
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>

            {activeModel && !activeModel.capabilities.includes("vision") && (
              <span className="text-2xs text-fg-subtle">Text only</span>
            )}
          </div>

          <button
            type="button"
            onClick={() =>
              applyTheme(theme === "dark" ? "light" : theme === "light" ? "system" : "dark")
            }
            aria-label={`Theme: ${theme}. Change theme.`}
            className="flex size-8 items-center justify-center rounded-control text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg"
          >
            {theme === "dark" ? (
              <MoonIcon className="size-4" />
            ) : theme === "light" ? (
              <SunIcon className="size-4" />
            ) : (
              <SunIcon className="size-4 opacity-60" />
            )}
          </button>
        </header>

        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="min-h-0 overflow-y-auto px-4 py-6 sm:px-6"
          aria-live="polite"
          aria-busy={streaming}
        >
          {banner && (
            <div
              role="status"
              className="measure mx-auto mb-4 flex items-start gap-2.5 rounded-panel bg-warn-soft px-3 py-2.5 text-sm text-warn-fg"
            >
              <AlertIcon className="mt-0.5 size-4 shrink-0" />
              <p className="flex-1">{banner}</p>
              <button
                type="button"
                onClick={() => setBanner(null)}
                className="text-2xs underline underline-offset-2"
              >
                Dismiss
              </button>
            </div>
          )}

          {messages.length === 0 ? (
            /* Centred rather than top-anchored: top-aligning left a large dead
               band above the composer, which reads as a broken layout. */
            <div className="measure mx-auto flex min-h-full max-w-[60ch] flex-col justify-center pb-12">
              <h1 className="text-2xl font-semibold tracking-tight text-fg">
                What are we looking into?
              </h1>
              <p className="mt-2 text-md leading-relaxed text-fg-muted">
                Ask a question, paste a link to read, or attach a document. Sources
                and timings are shown for every answer, and anything the model
                could not verify is said plainly.
              </p>
              <p className="mt-6 text-sm text-fg-subtle" dir="auto">
                يمكنك أيضًا الكتابة بالعربية — تتكيّف الواجهة تلقائيًا مع اتجاه النص.
              </p>
            </div>
          ) : (
            <div className="mx-auto flex flex-col gap-6">
              {messages.map((m) => {
                const isLast = m.seq === lastAssistantSeq;
                const isDraftPending =
                  m.role === "assistant" &&
                  m.content === "" &&
                  !m.error &&
                  !m.tool &&
                  streaming;
                if (isDraftPending) {
                  return (
                    <div key={m.id} className="measure mx-auto w-full">
                      <PendingIndicator elapsedMs={elapsed} />
                    </div>
                  );
                }
                // An assistant row with no text, no error and no stream is not
                // rendered at all — this is the "no empty card" guarantee.
                // An assistant row with no text, no error and no tool record
                // is never rendered — this is the "no empty card" guarantee.
                if (m.role === "assistant" && !m.content && !m.error && !m.tool)
                  return null;
                return (
                  <div key={m.id} className="mx-auto w-full max-w-[calc(72ch+2rem)]">
                    <MessageView
                      message={m}
                      isStreaming={streaming && isLast}
                      onRegenerate={
                        isLast && !streaming ? () => void onRegenerate() : undefined
                      }
                    />
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <Composer
          value={draft}
          onChange={setDraft}
          onSubmit={() => void onSubmit()}
          onStop={onStop}
          streaming={streaming}
          images={images}
          onImagesChange={setImages}
          visionSupported={!!activeModel?.capabilities.includes("vision")}
          onImageError={setBanner}
          files={files}
          uploading={uploading}
          onFiles={(f) => void onFiles(f)}
          onRemoveFile={onRemoveFile}
        />
      </main>
    </div>
  );
}

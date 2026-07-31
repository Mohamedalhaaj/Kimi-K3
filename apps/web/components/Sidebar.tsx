"use client";

import { useEffect, useRef, useState } from "react";
import type { Conversation } from "@/lib/types";
import { PencilIcon, PinIcon, PlusIcon, SearchIcon, TrashIcon } from "./icons";

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  loading: boolean;
  query: string;
  onQuery: (q: string) => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onTogglePin: (id: string, pinned: boolean) => void;
}

function Row({
  convo,
  active,
  onSelect,
  onRename,
  onDelete,
  onTogglePin,
}: {
  convo: Conversation;
  active: boolean;
  onSelect: () => void;
  onRename: (t: string) => void;
  onDelete: () => void;
  onTogglePin: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(convo.title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  function commit() {
    const next = draft.trim();
    setEditing(false);
    if (next && next !== convo.title) onRename(next);
    else setDraft(convo.title);
  }

  if (editing) {
    return (
      <li>
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") {
              setDraft(convo.title);
              setEditing(false);
            }
          }}
          aria-label="Conversation title"
          className="w-full rounded-control border border-accent-fg bg-bg-raised px-2.5 py-2 text-sm text-fg outline-none"
        />
      </li>
    );
  }

  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onSelect}
        aria-current={active ? "page" : undefined}
        className={`flex w-full items-center gap-2 rounded-control py-2 pe-16 ps-2.5 text-start text-sm transition-colors ${
          active
            ? "bg-bg-hover font-medium text-fg"
            : "text-fg-muted hover:bg-bg-hover hover:text-fg"
        }`}
      >
        {convo.pinned && (
          <PinIcon filled className="size-3.5 shrink-0 text-accent-fg" />
        )}
        <span className="truncate">{convo.title}</span>
      </button>

      {/* Row actions stay reachable by keyboard, not hover-only. */}
      <div className="absolute inset-y-0 end-1 flex items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
        <button
          type="button"
          onClick={onTogglePin}
          aria-label={convo.pinned ? "Unpin conversation" : "Pin conversation"}
          className="flex size-7 items-center justify-center rounded text-fg-subtle transition-colors hover:bg-border hover:text-fg"
        >
          <PinIcon filled={convo.pinned} className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={() => {
            setDraft(convo.title);
            setEditing(true);
          }}
          aria-label="Rename conversation"
          className="flex size-7 items-center justify-center rounded text-fg-subtle transition-colors hover:bg-border hover:text-fg"
        >
          <PencilIcon className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={onDelete}
          aria-label="Delete conversation"
          className="flex size-7 items-center justify-center rounded text-fg-subtle transition-colors hover:bg-border hover:text-danger"
        >
          <TrashIcon className="size-3.5" />
        </button>
      </div>
    </li>
  );
}

export function Sidebar({
  conversations,
  activeId,
  loading,
  query,
  onQuery,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onTogglePin,
}: Props) {
  return (
    <nav
      aria-label="Conversations"
      className="flex h-full flex-col border-e border-border bg-bg-sunken"
    >
      <div className="flex items-center justify-between gap-2 px-3 pb-2 pt-3">
        <span className="ps-1 text-sm font-semibold tracking-tight text-fg">
          Kimi Workspace
        </span>
      </div>

      <div className="px-3 pb-2">
        <button
          type="button"
          onClick={onNew}
          className="flex w-full items-center gap-2 rounded-control border border-border bg-bg-raised px-2.5 py-2 text-sm font-medium text-fg shadow-sm transition-colors hover:bg-bg-hover"
        >
          <PlusIcon className="size-4 text-accent-fg" />
          New chat
        </button>
      </div>

      <div className="relative px-3 pb-2">
        <SearchIcon className="pointer-events-none absolute start-5.5 top-1/2 size-3.5 -translate-y-1/2 text-fg-subtle" />
        <label htmlFor="convo-search" className="sr-only">
          Search conversations
        </label>
        <input
          id="convo-search"
          type="search"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Search"
          className="w-full rounded-control border border-transparent bg-bg-hover py-1.5 pe-2.5 ps-8 text-sm text-fg outline-none transition-colors focus:border-accent-fg focus:bg-bg-raised"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {loading ? (
          <ul className="space-y-1.5 pt-1" aria-label="Loading conversations">
            {[0, 1, 2, 3].map((i) => (
              <li key={i} className="skeleton h-8 rounded-control" />
            ))}
          </ul>
        ) : conversations.length === 0 ? (
          <p className="px-1 pt-4 text-xs leading-relaxed text-fg-subtle">
            {query
              ? `No conversation matches “${query}”.`
              : "Your conversations appear here. Start one with New chat."}
          </p>
        ) : (
          <ul className="space-y-0.5">
            {conversations.map((c) => (
              <Row
                key={c.id}
                convo={c}
                active={c.id === activeId}
                onSelect={() => onSelect(c.id)}
                onRename={(t) => onRename(c.id, t)}
                onDelete={() => onDelete(c.id)}
                onTogglePin={() => onTogglePin(c.id, !c.pinned)}
              />
            ))}
          </ul>
        )}
      </div>
    </nav>
  );
}

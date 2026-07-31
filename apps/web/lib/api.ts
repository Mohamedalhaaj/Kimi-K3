import type {
  ChatMode,
  Conversation,
  ConversationDetail,
  ModelInfo,
  StreamEvent,
} from "./types";

const BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8787";

/** An error that already carries a message safe to render to the user. */
export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly code: string = "internal",
    readonly retryable = false,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    // A dead backend is the single most likely local failure; name the fix.
    throw new ApiRequestError(
      "Cannot reach the Kimi API. Is the backend running on port 8787?",
      "network",
      true,
    );
  }

  if (res.status === 204) return undefined as T;

  if (!res.ok) {
    let code = "internal";
    let message = `Request failed (${res.status}).`;
    try {
      const body = await res.json();
      if (body?.error) {
        code = body.error.code ?? code;
        message = body.error.message ?? message;
      }
    } catch {
      /* keep the status-based fallback */
    }
    throw new ApiRequestError(message, code);
  }

  return (await res.json()) as T;
}

export const api = {
  listConversations: (q?: string) =>
    request<{ items: Conversation[]; page: { total: number } }>(
      `/api/conversations${q ? `?q=${encodeURIComponent(q)}` : ""}`,
    ),

  getConversation: (id: string) =>
    request<ConversationDetail>(`/api/conversations/${id}`),

  createConversation: (body: { title?: string; mode?: ChatMode } = {}) =>
    request<Conversation>("/api/conversations", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateConversation: (
    id: string,
    body: Partial<Pick<Conversation, "title" | "pinned" | "model_id" | "mode">>,
  ) =>
    request<Conversation>(`/api/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  deleteConversation: (id: string) =>
    request<void>(`/api/conversations/${id}`, { method: "DELETE" }),

  clearMessages: (id: string) =>
    request<void>(`/api/conversations/${id}/messages`, { method: "DELETE" }),

  models: () =>
    request<{ default: string; models: ModelInfo[] }>("/models"),
};

/**
 * Stream a chat turn.
 *
 * `EventSource` cannot POST, so this reads the SSE body off `fetch` directly.
 * The `signal` is what makes the Stop button real: aborting tears down the
 * socket, and the backend's disconnect check ends the provider call rather
 * than leaving an abandoned generation billing in the background.
 */
export async function* streamChat(
  body: {
    conversation_id: string;
    content: string;
    model_id?: string;
    mode?: ChatMode;
    images?: { data_url: string }[];
  },
  signal: AbortSignal,
): AsyncGenerator<StreamEvent> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/api/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if ((err as Error)?.name === "AbortError") return;
    throw new ApiRequestError(
      "Cannot reach the Kimi API. Is the backend running on port 8787?",
      "network",
      true,
    );
  }

  if (!res.ok || !res.body) {
    throw new ApiRequestError(
      `The server rejected the request (${res.status}).`,
      "internal",
    );
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line; the tail may be partial.
      let split: number;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);

        let event = "";
        let data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event: ")) event = line.slice(7).trim();
          else if (line.startsWith("data: ")) data += line.slice(6);
        }
        if (!event || !data) continue;
        try {
          yield { type: event, ...JSON.parse(data) } as StreamEvent;
        } catch {
          /* a malformed frame must not kill the stream */
        }
      }
    }
  } finally {
    // Always release, including on abort, so the connection actually closes.
    reader.cancel().catch(() => {});
  }
}

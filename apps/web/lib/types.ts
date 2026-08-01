export type ChatMode = "fast" | "balanced" | "deep";

export type Role = "user" | "assistant" | "system";

export type ResearchMode = "off" | "auto" | "always";

/** Mirrors kimi.tools.base.ToolStatus one-to-one. */
export type ToolStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_warnings"
  | "failed"
  | "cancelled"
  | "waiting_for_approval";

export type Renderer = "text" | "calculation" | "sources" | "article" | "json";

export interface ToolWarningOut {
  code: string;
  message: string;
}

export interface ToolInvocation {
  id?: string;
  tool_id: string;
  status: ToolStatus;
  arguments?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  warnings?: ToolWarningOut[];
  error?: ApiError | null;
  duration_ms?: number;
  renderer?: Renderer;
  reason?: string;
}

/** One numbered source, as rendered in the sources panel. */
export interface Citation {
  index: number;
  title: string;
  publisher: string;
  url: string;
  published_at: string | null;
  date_verified: boolean;
  provider: string;
  retrieval: string;
  status: string;
  status_label: string;
  excerpt: string;
  aggregator_url: string | null;
  note: string;
}

export interface ApiError {
  code: string;
  message: string;
  retryable?: boolean;
  detail?: string;
}

export interface Usage {
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
}

export interface Timing {
  first_token_ms: number | null;
  total_ms: number | null;
  /** The tool's own wall clock, reported separately from model time. */
  tool_ms?: number | null;
}

export interface ContextReport {
  included_messages: number;
  dropped_messages: number;
  estimated_prompt_tokens: number;
  budget_tokens: number;
  dropped_images: number;
}

export interface Message {
  id: string;
  conversation_id: string;
  seq: number;
  role: Role;
  content: string;
  model_id: string | null;
  usage: Usage | null;
  timing: Timing | null;
  error: ApiError | null;
  tool?: ToolInvocation | null;
  citations?: Citation[] | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  pinned: boolean;
  project_id: string | null;
  model_id: string | null;
  mode: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface ModelInfo {
  id: string;
  label: string;
  capabilities: string[];
  context_window: number;
}

/** Events emitted by POST /api/chat/stream. */
export type StreamEvent =
  | {
      type: "start";
      conversation_id: string;
      title: string;
      user_message_id: string;
      model_id: string;
      mode: ChatMode;
    }
  | ({ type: "tool" } & ToolInvocation)
  | { type: "sources"; sources: Citation[] }
  | ({ type: "context" } & ContextReport)
  | { type: "delta"; text: string }
  | { type: "warning"; code: string; message: string }
  | { type: "error"; code: string; message: string; retryable: boolean }
  | {
      type: "done";
      finish_reason: string | null;
      usage: Usage | null;
      timing: Timing | null;
      assistant_seq: number;
      model_called?: boolean;
      tool_ms?: number | null;
    };

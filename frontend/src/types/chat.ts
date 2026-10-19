export interface SourceItem {
  index: number;
  file_path: string;
  heading_path: string;
  excerpt: string;
  score: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: SourceItem[];
  model?: string | null;
  pending?: boolean;
  error?: string | null;
}

export type SSEEventName = "meta" | "sources" | "content" | "confirm" | "error" | "done";

export interface ConfirmPayload {
  original_question: string;
  rewritten_question: string;
  options: string[];
}

export interface MetaPayload {
  model: string;
  provider: string;
  task_type: string;
}

export interface DonePayload {
  session_id: string;
  level?: number;
  confidence?: number;
  fallback?: string;
}

export interface ModelOption {
  id: string;
  name: string;
  provider: string;
  type: string;
  available: boolean;
}

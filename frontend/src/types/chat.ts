export interface SourceItem {
  index: number;
  title?: string;          // V1.1 新增：简洁标题
  file_path: string;
  heading_path?: string;
  source_type?: string;    // V1.1 新增："tech_doc" | "personal_note"
  source_url?: string;     // V1.1 新增：爬虫文档原始 URL
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

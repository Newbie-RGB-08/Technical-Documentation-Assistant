export interface SourceItem {
  index: number;           // 代表片段序号（合并后为组内最小）
  title?: string;          // V1.1 新增：简洁标题
  file_path: string;
  heading_path?: string;
  source_type?: string;    // V1.1 新增："tech_doc" | "personal_note"
  source_url?: string;     // V1.1 新增：爬虫文档原始 URL
  excerpt: string;
  score: number;
  // V1.2 新增：同一文档多片段的合并信息（可选 → 旧数据按单片段降级）
  indices?: number[];      // 该文档覆盖的全部片段序号
  chunk_count?: number;    // 合并的片段数
  headings?: string[];     // 去重后的章节列表
  excerpts?: string[];     // 多片段摘要（后端最多 3 条）
  is_hybrid?: boolean;     // V1.3 新增：本地笔记与网络文章同文档合并标记
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

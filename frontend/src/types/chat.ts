export type AnswerStatus =
  | "pending"
  | "verified"
  | "extractive"
  | "partial"
  | "refused"
  | "unverified_general"
  | "unverified_general_flagged"
  | "confirm"
  | "interrupted"
  | "error";

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
  // V1.4 新增：来源类型、抓取时间与可信级别
  origin?: "local" | "web_search" | "hybrid";
  retrieved_at?: string;   // 网页抓取时间（本地来源为空）
  trust_level?: string;    // local_high | web_medium
  used?: boolean;          // V1.4 优化：本次回答是否实际引用了该来源
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: SourceItem[];
  model?: string | null;
  pending?: boolean;
  error?: string | null;
  // V1.4 新增：答案状态、中断标记与请求 ID
  answer_status?: AnswerStatus | null;
  interrupted?: boolean;
  request_id?: string;
}

export type SSEEventName =
  | "meta"
  | "status"
  | "sources"
  | "content"
  | "confirm"
  | "error"
  | "done";

export interface ConfirmPayload {
  original_question: string;
  rewritten_question: string;
  options: string[];
}

export interface MetaPayload {
  model: string;
  provider: string;
  // V1.5.x 新增：当前模型选择与 auto 标记（自动绑定兜底时 auto=true）
  selected?: { provider: string; model: string; auto: boolean } | null;
  task_type: string;
  complexity?: string | null;
  complexity_score?: number;
  route?: string | null;
  answer_mode?: string;
  web_enabled?: boolean;
  request_id?: string;
}

export interface StatusPayload {
  stage: string;
  complexity?: string;
  level?: number;
  answerability?: string;
  specificity?: string;
  web_chunks?: number;
  pages?: number;
}

export interface DonePayload {
  session_id: string;
  level?: number;
  confidence?: number;
  fallback?: string;
  answer_status?: string;
  verified?: boolean;
  fallback_level?: number;
  kb_written?: boolean;
  elapsed_ms?: number;
  request_id?: string;
  interrupted?: boolean;
  used_indices?: number[]; // V1.4 优化：回答实际引用的片段编号
}

export interface ModelOption {
  id: string;
  name: string;
  provider: string;
  type: string;
  available: boolean;
  // V1.5.x 新增（联合清单）：来源 / reason / 本地发现元数据（均可选 → 旧后端降级）
  source?: "config" | "discovered" | null;
  reason?: string | null;
  parameter_size?: number | null;
  family?: string | null;
  missing_models?: string[] | null;
}

/** V1.5.x：/api/providers/status 响应（Provider 健康快照 + 自动绑定） */
export interface ProviderStatusRow {
  name: string;
  type: "local" | "cloud";
  available: boolean;
  reason: string;
  detail?: string;
  installed_models: Array<{
    name: string;
    parameter_size: number | null;
    family: string | null;
    size_bytes?: number;
  }>;
  selected_for: string[];
}

export interface ProviderStatusReport {
  enabled: boolean;
  providers: ProviderStatusRow[];
  bindings: Record<string, { provider: string; model: string; reason: string } | null>;
}

/** 知识库上传相关类型（PRD 7.9 / 7.11）。 */

export type UploadFileStatus =
  | "queued"
  | "parsing"
  | "parsed"
  | "indexing"
  | "indexed"
  | "failed_parse"
  | "failed_index";

export type UploadTaskStatus = "queued" | "running" | "completed" | "partial" | "failed";

export interface UploadFile {
  upload_id: string;
  task_id?: string | null;
  original_filename: string;
  stored_path?: string;
  markdown_path?: string | null;
  doc_format: string;
  file_size: number;
  content_hash?: string;
  status: UploadFileStatus;
  auto_index: boolean;
  parse_engine?: string | null;
  chunk_size: number;
  chunk_overlap: number;
  progress?: number;
  error_message?: string | null;
  document_id?: number | null;
  chunk_count: number;
  created_at?: string;
  updated_at?: string;
  assets?: UploadAsset[];
}

export interface UploadAsset {
  asset_type: "image" | "table" | string;
  name: string;
  rel_path: string;
  page_no?: number | null;
  caption?: string | null;
  ocr_text?: string | null;
}

export interface UploadTask {
  task_id: string;
  status: UploadTaskStatus;
  engine: string;
  total_files: number;
  processed_files: number;
  failed_files: number;
  error_message?: string | null;
  created_at?: string;
  updated_at?: string;
  completed_at?: string | null;
  files?: UploadFile[];
}

export interface UploadListResponse {
  items: UploadFile[];
  total: number;
  limit: number;
  offset: number;
}

export interface UploadCreateItem {
  upload_id?: string | null;
  filename: string;
  format: string;
  size?: number | null;
  status: UploadFileStatus | "duplicate" | "rejected";
  error?: string | null;
}

export interface UploadCreateResponse {
  task_id: string;
  files: UploadCreateItem[];
}

export interface UploadActionResponse {
  task_id: string;
  status: string;
}

export interface UploadOptions {
  engine: string;
  auto_index: boolean;
  chunk_size: number;
  chunk_overlap: number;
  tags?: string;
  force?: boolean;
}

export interface EngineHealth {
  name?: string;
  available?: boolean;
  status?: string;
  detail?: string;
  latency_ms?: number | null;
  checked_at?: number;
  formats?: string[];
  [key: string]: unknown;
}

export interface EngineInfo {
  name: string;
  formats: string[];
  available: boolean;
  health?: EngineHealth;
}

export interface UploadListQuery {
  status?: string;
  format?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

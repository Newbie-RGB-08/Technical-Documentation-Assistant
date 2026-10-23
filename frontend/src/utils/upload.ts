import type { UploadFileStatus } from "../types/upload";

export const MAX_UPLOAD_MB = 100;
export const MAX_UPLOAD_FILES = 20;

/** 与后端 native 支持格式保持一致；图片在 P6 接入 OCR 后可用。 */
export const ACCEPTED_EXTENSIONS = [
  ".md",
  ".markdown",
  ".txt",
  ".html",
  ".htm",
  ".pdf",
  ".docx",
  ".pptx",
  ".xlsx",
  ".xls",
  ".csv",
  ".json",
  ".png",
  ".jpg",
  ".jpeg",
  ".bmp",
  ".tif",
  ".tiff",
  ".webp",
];

export const UPLOAD_ACCEPT = ACCEPTED_EXTENSIONS.join(",");

export function fileExtension(name: string): string {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

export function formatFileSize(size: number | null | undefined): string {
  if (size == null || !Number.isFinite(size)) return "-";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  return `${(size / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export const UPLOAD_STATUS_LABELS: Record<UploadFileStatus, string> = {
  queued: "排队中",
  parsing: "解析中",
  parsed: "已解析",
  indexing: "索引中",
  indexed: "已入库",
  failed_parse: "解析失败",
  failed_index: "索引失败",
};

export function uploadStatusLabel(status: string): string {
  return UPLOAD_STATUS_LABELS[status as UploadFileStatus] ?? status;
}

export function uploadStatusTone(status: string): "ok" | "warn" | "error" | "busy" | "muted" {
  if (status === "indexed") return "ok";
  if (status === "parsed") return "warn";
  if (status === "failed_parse" || status === "failed_index") return "error";
  if (status === "queued") return "muted";
  return "busy";
}

export function isBusyStatus(status: string): boolean {
  return status === "queued" || status === "parsing" || status === "indexing";
}

export function isFailedStatus(status: string): boolean {
  return status === "failed_parse" || status === "failed_index";
}

export function engineLabel(name: string): string {
  const labels: Record<string, string> = {
    auto: "自动选择",
    native: "Native 原生解析",
    rapidocr: "RapidOCR 本地 OCR",
    mineru: "MinerU",
    paddlex: "PaddleX",
  };
  return labels[name] ?? name;
}

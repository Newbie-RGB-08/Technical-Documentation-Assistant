import { createSSEParser, type ParsedSSE } from "./sse";
import type { ModelOption, ProviderStatusReport } from "../types/chat";
import type {
  EngineInfo,
  UploadActionResponse,
  UploadCreateResponse,
  UploadFile,
  UploadListQuery,
  UploadListResponse,
  UploadOptions,
  UploadTask,
} from "../types/upload";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

/** 把 FastAPI 的 {"detail": "..."} 错误转换为可展示文本。 */
export function parseApiError(text: string, status?: number): string {
  if (text) {
    try {
      const payload = JSON.parse(text) as {
        detail?: unknown;
        files?: Array<{ filename?: string; status?: string; error?: string }>;
      };
      if (typeof payload.detail === "string") {
        const rejected = (payload.files ?? [])
          .map((item) => item.error || `${item.filename ?? ""} ${item.status ?? ""}`.trim())
          .filter(Boolean);
        return rejected.length > 0
          ? `${payload.detail}\n${rejected.join("\n")}`
          : payload.detail;
      }
      if (payload.detail && typeof payload.detail === "object") {
        return JSON.stringify(payload.detail);
      }
    } catch {
      // 非 JSON 错误体按原文展示
    }
    return text;
  }
  return status ? `请求失败：${status}` : "请求失败";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, init);
  if (!resp.ok) {
    throw new Error(parseApiError(await resp.text(), resp.status));
  }
  return resp.json() as Promise<T>;
}

async function requestText(path: string, init?: RequestInit): Promise<string> {
  const resp = await fetch(`${API_BASE}${path}`, init);
  if (!resp.ok) {
    throw new Error(parseApiError(await resp.text(), resp.status));
  }
  return resp.text();
}

export function getModels(refresh = false): Promise<ModelOption[]> {
  return request<ModelOption[]>(`/models${refresh ? "?refresh=true" : ""}`);
}

/** V1.5.x：Provider 健康快照与自动绑定信息（错误诊断/横幅消费） */
export function getProviderStatus(refresh = false): Promise<ProviderStatusReport> {
  return request<ProviderStatusReport>(`/providers/status${refresh ? "?refresh=true" : ""}`);
}

export function getHealth(): Promise<{ status: string; checks: Record<string, boolean> }> {
  return request("/health");
}

export function postFeedback(payload: { session_id: string; question: string }) {
  return request<{ ok: boolean }>("/chat/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function ingestDirectory(payload: {
  directory: string;
  source_type?: string;
  force_reingest?: boolean;
}) {
  return request<{ task_id: string; status: string; file_count: number }>("/documents/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_type: "personal_note", ...payload }),
  });
}

// ---- 知识库上传（PRD 7.9）----
export function getUploads(query: UploadListQuery = {}): Promise<UploadListResponse> {
  const params = new URLSearchParams();
  if (query.status) params.set("status", query.status);
  if (query.format) params.set("format", query.format);
  if (query.q) params.set("q", query.q);
  if (query.limit != null) params.set("limit", String(query.limit));
  if (query.offset != null) params.set("offset", String(query.offset));
  const suffix = params.toString();
  return request<UploadListResponse>(`/uploads${suffix ? `?${suffix}` : ""}`);
}

export function getUploadEngines(): Promise<{ engines: EngineInfo[] }> {
  return request<{ engines: EngineInfo[] }>("/uploads/engines");
}

export function getUpload(uploadId: string): Promise<UploadFile> {
  return request<UploadFile>(`/uploads/${encodeURIComponent(uploadId)}`);
}

export function getUploadTask(taskId: string): Promise<UploadTask> {
  return request<UploadTask>(`/uploads/tasks/${encodeURIComponent(taskId)}`);
}

export function getUploadMarkdown(uploadId: string, signal?: AbortSignal): Promise<string> {
  return requestText(`/uploads/${encodeURIComponent(uploadId)}/markdown`, { signal });
}

export function reparseUpload(uploadId: string): Promise<UploadActionResponse> {
  return request<UploadActionResponse>(`/uploads/${encodeURIComponent(uploadId)}/reparse`, {
    method: "POST",
  });
}

export function reindexUpload(uploadId: string): Promise<UploadActionResponse> {
  return request<UploadActionResponse>(`/uploads/${encodeURIComponent(uploadId)}/reindex`, {
    method: "POST",
  });
}

export function deleteUpload(uploadId: string): Promise<{ ok: boolean; upload_id: string }> {
  return request(`/uploads/${encodeURIComponent(uploadId)}`, { method: "DELETE" });
}

export interface UploadRequestHandlers {
  onProgress?: (percent: number) => void;
  signal?: AbortSignal;
}

/** XHR 上传以获取真实上传进度；fetch 不支持上传进度事件。 */
export function uploadFiles(
  files: File[],
  options: UploadOptions,
  handlers: UploadRequestHandlers = {},
): Promise<UploadCreateResponse> {
  return new Promise((resolve, reject) => {
    if (handlers.signal?.aborted) {
      reject(new DOMException("上传已取消", "AbortError"));
      return;
    }
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    form.append("engine", options.engine || "auto");
    form.append("auto_index", String(options.auto_index));
    form.append("chunk_size", String(options.chunk_size));
    form.append("chunk_overlap", String(options.chunk_overlap));
    if (options.tags) form.append("tags", options.tags);
    if (options.force) form.append("force", "true");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/uploads`);
    const onAbort = () => xhr.abort();
    handlers.signal?.addEventListener("abort", onAbort, { once: true });

    const cleanup = () => {
      handlers.signal?.removeEventListener("abort", onAbort);
    };
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        handlers.onProgress?.(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onerror = () => {
      cleanup();
      reject(new Error("上传失败：网络错误"));
    };
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException("上传已取消", "AbortError"));
    };
    xhr.onload = () => {
      cleanup();
      const text = xhr.responseText || "";
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(parseApiError(text, xhr.status)));
        return;
      }
      try {
        resolve(JSON.parse(text) as UploadCreateResponse);
      } catch {
        reject(new Error("上传响应格式错误"));
      }
    };
    xhr.send(form);
  });
}

export interface StreamChatParams {
  question: string;
  sessionId?: string;
  topK?: number;
  modelId?: string | null;
  signal?: AbortSignal;
  onEvent: (event: ParsedSSE) => void;
}

export async function streamChat({
  question,
  sessionId,
  topK = 5,
  modelId,
  signal,
  onEvent,
}: StreamChatParams): Promise<void> {
  const resp = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      session_id: sessionId,
      top_k: topK,
      model_id: modelId ?? undefined,
    }),
    signal,
  });
  if (!resp.ok) throw new Error(`对话请求失败：${resp.status}`);
  if (!resp.body) throw new Error("当前环境不支持 ReadableStream");

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  const parser = createSSEParser(onEvent);
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parser.push(decoder.decode(value, { stream: true }));
  }
  parser.push(decoder.decode());
  parser.flush();
}

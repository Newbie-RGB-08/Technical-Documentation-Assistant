import { createSSEParser, type ParsedSSE } from "./sse";
import type { ModelOption } from "../types/chat";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, init);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `请求失败：${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

export function getModels(): Promise<ModelOption[]> {
  return request<ModelOption[]>("/models");
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

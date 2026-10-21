/**
 * V1.2：SSE sources 事件里的合并卡片必须原样进入状态。
 * 若这里对 indices 做裁剪/重排，正文 [来源N] 的联动就会错位。
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useChatStream } from "./useChatStream";
import type { SourceItem } from "../types/chat";

vi.mock("../api/client", () => ({
  streamChat: vi.fn(),
  postFeedback: vi.fn(() => Promise.resolve({ ok: true })),
}));

import { streamChat } from "../api/client";

const MERGED: SourceItem = {
  index: 3,
  indices: [3, 4, 7, 9],
  chunk_count: 4,
  title: "3. Python 演示 — Python 3.15.0 文档",
  file_path: "D:/docs/python-3.15.0-tutorial/introduction.html",
  heading_path: "3.1 基础",
  headings: ["3.1 基础", "3.2 流程"],
  source_type: "tech_doc",
  source_url: "https://docs.python.org/zh-cn/3.15/tutorial/introduction.html",
  excerpt: "第一段摘要",
  excerpts: ["第一段摘要", "第二段摘要"],
  score: 0.094,
};

describe("useChatStream V1.4 状态与重试", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("status 事件更新阶段，done 写入 answer_status/request_id", async () => {
    (streamChat as any).mockImplementation(
      async ({ onEvent }: { onEvent: (e: any) => void }) => {
        onEvent({ event: "meta", data: { model: "m", provider: "p", task_type: "generate" } });
        onEvent({ event: "status", data: { stage: "verifying" } });
        onEvent({ event: "content", data: { text: "答案正文" } });
        onEvent({
          event: "done",
          data: {
            session_id: "sess-v14",
            answer_status: "extractive",
            verified: false,
            fallback_level: 2,
            request_id: "req-1",
            interrupted: false,
          },
        });
      },
    );

    const { result } = renderHook(() => useChatStream(null));
    await act(async () => {
      await result.current.send("ChromaDB 如何持久化？");
    });

    await waitFor(() => expect(result.current.streaming).toBe(false));
    const assistant = result.current.messages.find((m) => m.role === "assistant")!;
    expect(assistant.answer_status).toBe("extractive");
    expect(assistant.request_id).toBe("req-1");
    expect(assistant.interrupted).toBe(false);
    expect(result.current.stage).toBeNull();
  });

  it("中断后 retry 重发同一个问题", async () => {
    const calls: string[] = [];
    (streamChat as any).mockImplementation(
      async ({ question, onEvent }: { question: string; onEvent: (e: any) => void }) => {
        calls.push(question);
        if (calls.length === 1) {
          onEvent({ event: "content", data: { text: "部分内容" } });
          onEvent({
            event: "done",
            data: { session_id: "sess-retry", answer_status: "interrupted", interrupted: true },
          });
        } else {
          onEvent({
            event: "done",
            data: { session_id: "sess-retry", answer_status: "verified", verified: true },
          });
        }
      },
    );

    const { result } = renderHook(() => useChatStream(null));
    await act(async () => {
      await result.current.send("ChromaDB 如何持久化？");
    });
    await waitFor(() => expect(result.current.streaming).toBe(false));
    const first = result.current.messages.find((m) => m.role === "assistant")!;
    expect(first.interrupted).toBe(true);
    expect(first.answer_status).toBe("interrupted");

    await act(async () => {
      result.current.retry();
    });
    await waitFor(() => expect(calls).toEqual(["ChromaDB 如何持久化？", "ChromaDB 如何持久化？"]));
    await waitFor(() => {
      const assistants = result.current.messages.filter((m) => m.role === "assistant");
      expect(assistants[assistants.length - 1].answer_status).toBe("verified");
    });
  });
});

describe("useChatStream 来源合并透传（V1.2）", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("SSE sources 事件原样保存合并卡片，indices 不被裁剪", async () => {
    (streamChat as any).mockImplementation(
      async ({ onEvent }: { onEvent: (e: any) => void }) => {
        onEvent({ event: "sources", data: [MERGED] });
        onEvent({ event: "content", data: { text: "根据文档 [来源7] ..." } });
        onEvent({ event: "done", data: { session_id: "sess-merge" } });
      }
    );

    const { result } = renderHook(() => useChatStream(null));
    await act(async () => {
      await result.current.send("Python 演示章节讲了什么？");
    });

    await waitFor(() => expect(result.current.streaming).toBe(false));
    expect(result.current.sources).toHaveLength(1);
    expect(result.current.sources[0]).toEqual(MERGED);
    expect(result.current.sources[0].indices).toEqual([3, 4, 7, 9]);
  });
});

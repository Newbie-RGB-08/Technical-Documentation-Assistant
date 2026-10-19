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

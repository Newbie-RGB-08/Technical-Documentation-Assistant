/**
 * V1.2：来源面板的合并展示与引用高亮联动。
 * 核心断言：点正文 [来源N]（N 可以是组内任意片段序号）→ 同一张文档卡片高亮。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import CitationPanel from "./CitationPanel";
import type { SourceItem } from "../types/chat";

const MERGED: SourceItem = {
  index: 3,
  indices: [3, 4, 7, 9],
  chunk_count: 4,
  title: "3. Python 演示 — Python 3.15.0 文档",
  file_path: "D:/docs/python-3.15.0-tutorial/introduction.html",
  heading_path: "/ 3. Python 演示 / 3.1 基础",
  headings: [
    "/ 3. Python 演示 / 3.1 基础",
    "/ 3. Python 演示 / 3.2 流程",
    "/ 3. Python 演示 / 3.4 进阶",
    "/ 3. Python 演示 / 3.5 案例",
  ],
  source_type: "tech_doc",
  source_url: "https://docs.python.org/zh-cn/3.15/tutorial/introduction.html",
  excerpt: "第一段摘要",
  excerpts: ["第一段摘要", "第二段摘要", "第三段摘要"],
  score: 0.094,
};

const LEGACY: SourceItem = {
  // 旧数据：无 indices/headings/excerpts，必须按单片段降级
  index: 1,
  file_path: "D:/notes/legacy.md",
  excerpt: "旧数据摘要",
  score: 0.5,
};

function renderPanel(sources: SourceItem[], activeIndex: number | null) {
  const onSelect = vi.fn();
  const utils = render(
    <CitationPanel
      sources={sources}
      activeIndex={activeIndex}
      open
      onClose={() => undefined}
      onSelect={onSelect}
    />
  );
  return { onSelect, container: utils.container, unmount: utils.unmount };
}

describe("CitationPanel 合并展示", () => {
  let openSpy: any;
  let fetchSpy: any;

  beforeEach(() => {
    openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => Promise.resolve(new Response("{}")));
  });

  afterEach(() => {
    openSpy.mockRestore();
    fetchSpy.mockRestore();
  });

  it("同一文档 4 个片段 → 只渲染 1 张卡片，副标题双口径", () => {
    const { container } = renderPanel([MERGED], null);
    expect(container.querySelectorAll(".source-card")).toHaveLength(1);
    expect(container.querySelector(".panel-subtitle")?.textContent).toBe(
      "使用了 1 篇文档 / 4 个片段"
    );
  });

  it("activeIndex 命中组内任意片段 → 同一张卡片高亮", () => {
    for (const idx of [3, 4, 7, 9]) {
      const { container, unmount } = renderPanel([MERGED], idx);
      const cards = container.querySelectorAll(".source-card");
      expect(cards).toHaveLength(1);
      expect(cards[0].classList.contains("active")).toBe(true);
      unmount();
    }
  });

  it("activeIndex 不属于任何片段 → 无卡片高亮", () => {
    const { container } = renderPanel([MERGED], 999);
    expect(container.querySelectorAll(".source-card.active")).toHaveLength(0);
  });

  it("多文档混合：卡片数 = 文档数，片段数 = 各 chunk_count 之和", () => {
    const { container } = renderPanel([MERGED, LEGACY], null);
    expect(container.querySelectorAll(".source-card")).toHaveLength(2);
    expect(container.querySelector(".panel-subtitle")?.textContent).toBe(
      "使用了 2 篇文档 / 5 个片段"
    );
  });

  it("旧数据（无 indices）按 index 精确匹配", () => {
    const hit = renderPanel([LEGACY], 1);
    expect(hit.container.querySelectorAll(".source-card.active")).toHaveLength(1);
    const miss = renderPanel([LEGACY], 2);
    expect(miss.container.querySelectorAll(".source-card.active")).toHaveLength(0);
  });

  it("点击卡片 → onSelect 传代表片段序号", () => {
    const { container, onSelect } = renderPanel([MERGED], null);
    fireEvent.click(container.querySelector(".source-card") as HTMLElement);
    expect(onSelect).toHaveBeenCalledWith(3);
  });
});

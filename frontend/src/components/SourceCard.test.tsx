/**
 * V1.1：SourceCard 渲染与点击行为分支。
 * 按 PRD 附录 B.4.2 / B.6 验收 #1/#2/#3/#5 覆盖。
 */
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import SourceCard from "./SourceCard";
import type { SourceItem } from "../types/chat";

const WEB_SOURCE: SourceItem = {
  index: 1,
  title: "FastAPI 依赖注入实战",
  file_path: "D:/docs/fastapi_depends.md",
  heading_path: "/ 基础 / Depends",
  source_type: "tech_doc",
  source_url: "https://fastapi.tiangolo.com/tutorial/dependencies/",
  excerpt: "FastAPI 的 Depends 可以注入……",
  score: 0.92,
};

const LOCAL_SOURCE: SourceItem = {
  index: 2,
  title: "Ollama 运维笔记",
  file_path: "D:/notes/ollama_ops.md",
  heading_path: "常见命令",
  source_type: "personal_note",
  source_url: "",
  excerpt: "ollama pull 之后可以……",
  score: 0.88,
};

const LOCAL_NO_TYPE: SourceItem = {
  // 兜底场景：老数据没有 source_type / source_url
  index: 3,
  file_path: "D:/legacy/old.md",
  excerpt: "旧数据",
  score: 0.7,
};

describe("SourceCard 渲染", () => {
  it("显示 title 与来源徽章", () => {
    render(<SourceCard source={WEB_SOURCE} />);
    expect(screen.getByText("FastAPI 依赖注入实战")).toBeInTheDocument();
    expect(screen.getByText("网页文档")).toBeInTheDocument();
    expect(screen.getByText("[1]")).toBeInTheDocument();
  });

  it("本地笔记显示 '本地笔记' 徽章", () => {
    render(<SourceCard source={LOCAL_SOURCE} />);
    expect(screen.getByText("本地笔记")).toBeInTheDocument();
    expect(screen.getByText("📄 点击在本地打开")).toBeInTheDocument();
  });

  it("网页来源显示 ↗ 提示", () => {
    render(<SourceCard source={WEB_SOURCE} />);
    expect(screen.getByText(/↗ 点击跳转原始网页/)).toBeInTheDocument();
  });

  it("兜底（无 source_type）→ 走本地路径", () => {
    render(<SourceCard source={LOCAL_NO_TYPE} />);
    expect(screen.getByText("本地笔记")).toBeInTheDocument();
    expect(screen.getByText("📄 点击在本地打开")).toBeInTheDocument();
  });

  it("title 为空时回退到文件名 stem", () => {
    const s: SourceItem = { ...WEB_SOURCE, title: "" };
    render(<SourceCard source={s} />);
    // file_path 最后一段 fastapi_depends.md 在 displayTitle 和 source-path 两处都出现 → 用 getAll
    const hits = screen.getAllByText(/fastapi_depends/);
    expect(hits.length).toBeGreaterThanOrEqual(1);
    // displayTitle 应该正好是文件名 stem（不带目录）
    expect(hits.some((el) => el.textContent === "fastapi_depends.md")).toBe(true);
  });
});

describe("SourceCard 点击行为", () => {
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

  it("tech_doc + source_url → window.open 新标签", () => {
    render(<SourceCard source={WEB_SOURCE} />);
    fireEvent.click(screen.getByRole("button"));
    expect(openSpy).toHaveBeenCalledWith(
      "https://fastapi.tiangolo.com/tutorial/dependencies/",
      "_blank",
      "noopener,noreferrer"
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("personal_note → fetch /api/files/open", () => {
    render(<SourceCard source={LOCAL_SOURCE} />);
    fireEvent.click(screen.getByRole("button"));
    expect(openSpy).not.toHaveBeenCalled();
    expect(fetchSpy).toHaveBeenCalledWith(
      `/api/files/open?path=${encodeURIComponent("D:/notes/ollama_ops.md")}`
    );
  });

  it("兜底（无 source_type）→ fetch /api/files/open", () => {
    render(<SourceCard source={LOCAL_NO_TYPE} />);
    fireEvent.click(screen.getByRole("button"));
    expect(fetchSpy).toHaveBeenCalledWith(
      `/api/files/open?path=${encodeURIComponent("D:/legacy/old.md")}`
    );
  });

  it("onClick prop 先触发（父组件 CitationPanel 激活）", () => {
    const parentCb = vi.fn();
    render(<SourceCard source={WEB_SOURCE} onClick={parentCb} />);
    fireEvent.click(screen.getByRole("button"));
    expect(parentCb).toHaveBeenCalledTimes(1);
    // 同时也触发 window.open
    expect(openSpy).toHaveBeenCalledTimes(1);
  });
});

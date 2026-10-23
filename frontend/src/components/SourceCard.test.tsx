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

describe("SourceCard 合并展示（V1.2）", () => {
  const MERGED: SourceItem = {
    index: 3,
    indices: [3, 4, 7, 9],
    chunk_count: 4,
    title: "3. Python 演示 — Python 3.15.0 文档",
    file_path: "D:/docs/python-3.15.0-tutorial/introduction.html",
    heading_path: "3.1 基础",
    headings: ["3.1 基础", "3.2 流程", "3.4 进阶", "3.5 案例"],
    source_type: "tech_doc",
    source_url: "https://docs.python.org/zh-cn/3.15/tutorial/introduction.html",
    excerpt: "第一段摘要",
    excerpts: ["第一段摘要", "第二段摘要", "第三段摘要"],
    score: 0.094,
  };

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

  it("展示「共 N 段」徽章，且不再渲染章节列表（V1.3）", () => {
    render(<SourceCard source={MERGED} />);
    expect(screen.getByText("共 4 段")).toBeInTheDocument();
    // V1.3：章节列表已移除，章节文本不应作为可见文本出现
    for (const heading of MERGED.headings!) {
      expect(screen.queryByText(heading)).not.toBeInTheDocument();
    }
  });

  it("不渲染完整本地路径，仅显示文件名（V1.3）", () => {
    render(<SourceCard source={MERGED} />);
    // 完整路径不应出现在 DOM 文本中
    expect(screen.queryByText(MERGED.file_path)).not.toBeInTheDocument();
    // 仅显示文件名
    expect(screen.getByText("introduction.html")).toBeInTheDocument();
  });

  it("不渲染网页 URL 明文（V1.3）", () => {
    render(<SourceCard source={MERGED} />);
    expect(screen.queryByText(/https?:\/\//)).not.toBeInTheDocument();
  });

  it("摘要默认折叠 2 条，可展开剩余 1 段", () => {
    render(<SourceCard source={MERGED} />);
    expect(screen.getByText("第一段摘要")).toBeInTheDocument();
    expect(screen.getByText("第二段摘要")).toBeInTheDocument();
    expect(screen.queryByText("第三段摘要")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "展开剩余 1 段" }));
    expect(screen.getByText("第三段摘要")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收起" })).toBeInTheDocument();
  });

  it("展开/收起只切换摘要，不触发跳转", () => {
    render(<SourceCard source={MERGED} />);
    fireEvent.click(screen.getByRole("button", { name: "展开剩余 1 段" }));
    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    expect(openSpy).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("合并卡片仍保留 V1.1 的网页跳转行为", () => {
    render(<SourceCard source={MERGED} />);
    const card = screen
      .getByText("3. Python 演示 — Python 3.15.0 文档")
      .closest(".source-card") as HTMLElement;
    fireEvent.click(card);
    expect(openSpy).toHaveBeenCalledWith(
      "https://docs.python.org/zh-cn/3.15/tutorial/introduction.html",
      "_blank",
      "noopener,noreferrer"
    );
  });

  it("单片段不显示徽章与摘要展开按钮，且不渲染章节（V1.3）", () => {
    render(<SourceCard source={LOCAL_SOURCE} />);
    expect(screen.queryByText(/共 \d+ 段/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /展开剩余/ })).not.toBeInTheDocument();
    // V1.3：章节不再渲染
    expect(screen.queryByText(/章节：/)).not.toBeInTheDocument();
  });
});

describe("SourceCard V1.4 引用标记", () => {
  it("未被回答引用的来源显示「未引用」并置灰", () => {
    render(<SourceCard source={{ ...LOCAL_SOURCE, used: false }} />);
    expect(screen.getByText("未引用")).toBeInTheDocument();
    expect(screen.getByText("Ollama 运维笔记").closest(".source-card")).toHaveClass("unused");
  });

  it("被回答引用的来源不显示「未引用」", () => {
    render(<SourceCard source={{ ...LOCAL_SOURCE, used: true }} />);
    expect(screen.queryByText("未引用")).not.toBeInTheDocument();
    expect(screen.getByText("Ollama 运维笔记").closest(".source-card")).not.toHaveClass("unused");
  });
});

describe("SourceCard V1.4 联网徽章与抓取时间", () => {
  const ONLINE: SourceItem = {
    index: 2,
    title: "Chroma 持久化指南",
    file_path: "D:/docs/tech_docs/web/example.com-abcdef12.md",
    source_type: "tech_doc",
    source_url: "https://example.com/chroma",
    origin: "web_search",
    trust_level: "web_medium",
    retrieved_at: "2026-03-01T10:00:00+08:00",
    excerpt: "ChromaDB 通过本地目录持久化。",
    score: 0.5,
  };

  it("联网来源显示「联网检索」徽章与抓取日期", () => {
    render(<SourceCard source={ONLINE} />);
    expect(screen.getByText("联网检索")).toBeInTheDocument();
    expect(screen.getByText("抓取于 2026-03-01")).toBeInTheDocument();
  });

  it("本地来源不显示联网徽章", () => {
    render(<SourceCard source={LOCAL_SOURCE} />);
    expect(screen.queryByText("联网检索")).not.toBeInTheDocument();
    expect(screen.queryByText(/抓取于/)).not.toBeInTheDocument();
  });

  it("本地来源即使带 source_url 也不误标为联网", () => {
    const local: SourceItem = {
      ...LOCAL_SOURCE,
      source_url: "https://example.com/local",
      origin: "local",
    };
    render(<SourceCard source={local} />);
    expect(screen.queryByText("联网检索")).not.toBeInTheDocument();
  });
});

describe("SourceCard 上传文档分支（V1.6）", () => {
  const UPLOAD_SOURCE: SourceItem = {
    index: 4,
    title: "产品手册",
    file_path: "data/uploads/u_20261023_ab12cd34/产品手册.pdf",
    source_type: "uploaded_doc",
    origin: "upload",
    upload_id: "u_20261023_ab12cd34",
    doc_format: "pdf",
    original_filename: "产品手册-完整版.pdf",
    excerpt: "上传文档的解析摘要",
    score: 0.81,
  };

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

  it("显示上传文档徽章、预览提示与原始文件名，不显示联网徽章", () => {
    render(<SourceCard source={UPLOAD_SOURCE} />);
    expect(screen.getByText("上传文档")).toBeInTheDocument();
    expect(screen.getByText("📄 点击预览解析结果")).toBeInTheDocument();
    expect(screen.getAllByText("产品手册-完整版.pdf").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("联网检索")).not.toBeInTheDocument();
  });

  it("点击优先调用 onOpenUpload，且不调用 /api/files/open", () => {
    const onOpenUpload = vi.fn();
    render(<SourceCard source={UPLOAD_SOURCE} onOpenUpload={onOpenUpload} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onOpenUpload).toHaveBeenCalledWith("u_20261023_ab12cd34");
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(openSpy).not.toHaveBeenCalled();
  });

  it("未提供 onOpenUpload 时回退打开 Markdown，不调用本地打开接口", () => {
    render(<SourceCard source={UPLOAD_SOURCE} />);
    fireEvent.click(screen.getByRole("button"));
    expect(openSpy).toHaveBeenCalledWith(
      "/api/uploads/u_20261023_ab12cd34/markdown",
      "_blank",
      "noopener,noreferrer",
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("onClick 与上传预览回调同时触发（来源面板激活 + 预览）", () => {
    const onClick = vi.fn();
    const onOpenUpload = vi.fn();
    render(
      <SourceCard source={UPLOAD_SOURCE} onClick={onClick} onOpenUpload={onOpenUpload} />,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(onOpenUpload).toHaveBeenCalledTimes(1);
  });
});

describe("SourceCard hybrid 双入口（V1.3）", () => {
  const HYBRID: SourceItem = {
    index: 1,
    indices: [1, 2],
    chunk_count: 2,
    title: "ChromaDB 使用要点",
    file_path: "D:/docs/notes/chromadb_usage.md",
    source_type: "personal_note",
    source_url: "https://docs.trychroma.com/getting-started",
    is_hybrid: true,
    excerpt: "本地摘要",
    excerpts: ["本地摘要", "网页摘要"],
    score: 0.95,
  };

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

  it("渲染「本地打开」与「网页原文」两个按钮", () => {
    render(<SourceCard source={HYBRID} />);
    expect(screen.getByRole("button", { name: "📄 本地打开" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "↗ 网页原文" })).toBeInTheDocument();
  });

  it("点击「本地打开」调用 /api/files/open，不打开网页", () => {
    render(<SourceCard source={HYBRID} />);
    fireEvent.click(screen.getByRole("button", { name: "📄 本地打开" }));
    expect(fetchSpy).toHaveBeenCalledWith(
      `/api/files/open?path=${encodeURIComponent(HYBRID.file_path)}`
    );
    expect(openSpy).not.toHaveBeenCalled();
  });

  it("点击「网页原文」调用 window.open，不调本地接口", () => {
    render(<SourceCard source={HYBRID} />);
    fireEvent.click(screen.getByRole("button", { name: "↗ 网页原文" }));
    expect(openSpy).toHaveBeenCalledWith(
      HYBRID.source_url,
      "_blank",
      "noopener,noreferrer"
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("hybrid 卡片整体点击不触发跳转（避免与显式按钮歧义）", () => {
    render(<SourceCard source={HYBRID} />);
    const card = screen.getByText("ChromaDB 使用要点").closest(".source-card") as HTMLElement;
    fireEvent.click(card);
    expect(openSpy).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("不暴露完整路径与 URL 明文", () => {
    render(<SourceCard source={HYBRID} />);
    expect(screen.queryByText(HYBRID.file_path)).not.toBeInTheDocument();
    expect(screen.queryByText(/https?:\/\//)).not.toBeInTheDocument();
  });
});

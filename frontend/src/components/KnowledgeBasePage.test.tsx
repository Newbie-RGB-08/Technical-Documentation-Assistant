import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import KnowledgeBasePage from "./KnowledgeBasePage";
import type { EngineInfo, UploadFile } from "../types/upload";

vi.mock("../api/client", () => ({
  getUploads: vi.fn(),
  getUploadEngines: vi.fn(),
  uploadFiles: vi.fn(),
  getUploadMarkdown: vi.fn(),
  reparseUpload: vi.fn(),
  reindexUpload: vi.fn(),
  deleteUpload: vi.fn(),
  getUpload: vi.fn(),
  getUploadTask: vi.fn(),
}));

const mocked = vi.mocked(client);

const INDEXED: UploadFile = {
  upload_id: "u_1",
  task_id: "t_1",
  original_filename: "产品手册.pdf",
  stored_path: "backend/data/uploads/u_1/产品手册.pdf",
  markdown_path: "backend/data/uploads/u_1/parsed.md",
  doc_format: "pdf",
  file_size: 1572864,
  status: "indexed",
  auto_index: true,
  parse_engine: "native",
  chunk_size: 512,
  chunk_overlap: 64,
  progress: 100,
  document_id: 7,
  chunk_count: 12,
  error_message: null,
};

const FAILED_PARSE: UploadFile = {
  ...INDEXED,
  upload_id: "u_failed",
  original_filename: "损坏文档.pdf",
  status: "failed_parse",
  markdown_path: null,
  chunk_count: 0,
  progress: 0,
  error_message: "解析失败：PDF 已加密",
};

const PARSED: UploadFile = {
  ...INDEXED,
  upload_id: "u_parsed",
  original_filename: "待索引.md",
  doc_format: "md",
  status: "parsed",
  progress: 60,
  chunk_count: 0,
};

const ENGINES: EngineInfo[] = [
  { name: "native", available: true, formats: [".md", ".pdf"], health: { status: "ok" } },
  {
    name: "rapidocr",
    available: false,
    formats: [],
    health: { status: "unavailable", detail: "rapidocr 未安装" },
  },
];

function listResult(items: UploadFile[] = []) {
  return { items, total: items.length, limit: 100, offset: 0 };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getUploads.mockResolvedValue(listResult());
  mocked.getUploadEngines.mockResolvedValue({ engines: ENGINES });
  mocked.getUploadMarkdown.mockResolvedValue("# 解析标题\n\n正文内容");
  mocked.uploadFiles.mockResolvedValue({ task_id: "t_upload", files: [] });
  mocked.reparseUpload.mockResolvedValue({ task_id: "t_reparse", status: "queued" });
  mocked.reindexUpload.mockResolvedValue({ task_id: "t_reindex", status: "queued" });
  mocked.deleteUpload.mockResolvedValue({ ok: true, upload_id: "u_1" });
});

describe("KnowledgeBasePage 页签与上传", () => {
  it("提供上传/文档/引擎三个页签，并可返回对话", async () => {
    render(<KnowledgeBasePage pollIntervalMs={60_000} />);
    await screen.findByText("还没有上传文档");

    expect(screen.getByRole("tab", { name: /^上传/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^文档/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^引擎/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回对话" })).toHaveAttribute("href", "#/chat");

    fireEvent.click(screen.getByRole("tab", { name: /^上传/ }));
    expect(screen.getByLabelText("拖拽或选择文件")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /^引擎/ }));
    expect(await screen.findByText("Native 原生解析")).toBeInTheDocument();
    expect(screen.getByText("rapidocr 未安装")).toBeInTheDocument();
    expect(screen.getAllByText("不可用").length).toBeGreaterThanOrEqual(1);
  });

  it("选择文件后按表单参数上传，成功后切到文档页", async () => {
    const file = new File(["# hello"], "hello.md", { type: "text/markdown" });
    render(<KnowledgeBasePage pollIntervalMs={60_000} />);
    await screen.findByText("还没有上传文档");

    fireEvent.click(screen.getByRole("tab", { name: /^上传/ }));
    fireEvent.change(screen.getByLabelText("选择文件"), { target: { files: [file] } });
    expect(await screen.findByText("hello.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始上传" }));

    await waitFor(() => expect(mocked.uploadFiles).toHaveBeenCalledTimes(1));
    const [files, options] = mocked.uploadFiles.mock.calls[0];
    expect(files).toHaveLength(1);
    expect(files[0].name).toBe("hello.md");
    expect(options).toMatchObject({
      engine: "auto",
      auto_index: true,
      chunk_size: 512,
      chunk_overlap: 64,
    });
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /^文档/ })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });
  });

  it("本地拒绝未知扩展名，不调用上传接口", async () => {
    render(<KnowledgeBasePage pollIntervalMs={60_000} />);
    await screen.findByText("还没有上传文档");
    fireEvent.click(screen.getByRole("tab", { name: /^上传/ }));
    const file = new File(["MZ"], "bad.exe", { type: "application/octet-stream" });
    fireEvent.change(screen.getByLabelText("选择文件"), { target: { files: [file] } });

    expect(await screen.findByText(/不支持 \.exe/)).toBeInTheDocument();
    expect(screen.getByText("上传前校验失败")).toBeInTheDocument();
    expect(mocked.uploadFiles).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "开始上传" })).toBeDisabled();
  });
});

describe("KnowledgeBasePage 文档列表与操作", () => {
  it("展示文件名/状态/格式/块数/引擎，并打开 Markdown 预览", async () => {
    mocked.getUploads.mockResolvedValue(listResult([INDEXED]));
    mocked.getUploadMarkdown.mockResolvedValue(
      "# 解析标题\n\n正文内容\n\n![示意图](assets/image_001.png)",
    );
    render(<KnowledgeBasePage pollIntervalMs={60_000} />);

    expect(await screen.findByText("产品手册.pdf")).toBeInTheDocument();
    expect(screen.getByText("已入库")).toBeInTheDocument();
    expect(screen.getByText("PDF")).toBeInTheDocument();
    expect(screen.getByText("1.5 MB")).toBeInTheDocument();
    expect(screen.getByText("12 块")).toBeInTheDocument();
    expect(screen.getByText("Native 原生解析")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    const dialog = await screen.findByRole("dialog", { name: "上传文档预览" });
    expect(await within(dialog).findByText("解析标题")).toBeInTheDocument();
    expect(mocked.getUploadMarkdown).toHaveBeenCalledWith("u_1", expect.any(AbortSignal));
    const image = dialog.querySelector("img");
    expect(image).toHaveAttribute("src", "/api/uploads/u_1/assets/image_001.png");
    expect(within(dialog).getByRole("link", { name: "打开原文件" })).toHaveAttribute(
      "href",
      "/api/uploads/u_1/original",
    );

    fireEvent.click(within(dialog).getByRole("button", { name: "关闭预览" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("解析失败可重试，并显示可复制错误文本", async () => {
    mocked.getUploads.mockResolvedValue(listResult([FAILED_PARSE]));
    render(<KnowledgeBasePage pollIntervalMs={60_000} />);

    expect(await screen.findByText("损坏文档.pdf")).toBeInTheDocument();
    expect(screen.getByText("解析失败")).toBeInTheDocument();
    expect(screen.getByText("解析失败：PDF 已加密")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() =>
      expect(mocked.reparseUpload).toHaveBeenCalledWith("u_failed"),
    );
  });

  it("已解析文档可重建索引", async () => {
    mocked.getUploads.mockResolvedValue(listResult([PARSED]));
    render(<KnowledgeBasePage pollIntervalMs={60_000} />);

    expect(await screen.findByText("待索引.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重建索引" }));
    await waitFor(() => expect(mocked.reindexUpload).toHaveBeenCalledWith("u_parsed"));
  });

  it("删除前确认，删除后刷新列表", async () => {
    mocked.getUploads.mockResolvedValue(listResult([INDEXED]));
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<KnowledgeBasePage pollIntervalMs={60_000} />);

    expect(await screen.findByText("产品手册.pdf")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mocked.deleteUpload).toHaveBeenCalledWith("u_1"));
    expect(confirmSpy).toHaveBeenCalled();
    expect(mocked.getUploads.mock.calls.length).toBeGreaterThanOrEqual(2);
    confirmSpy.mockRestore();
  });

  it("列表接口失败时展示错误并可重试", async () => {
    mocked.getUploads
      .mockRejectedValueOnce(new Error("列表网络错误"))
      .mockResolvedValue(listResult());
    render(<KnowledgeBasePage pollIntervalMs={60_000} />);

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/列表网络错误/)).toBeInTheDocument();
    fireEvent.click(within(alert).getByRole("button", { name: "重试" }));
    await waitFor(() => expect(mocked.getUploads.mock.calls.length).toBeGreaterThanOrEqual(2));
    await screen.findByText("还没有上传文档");
  });
});

describe("KnowledgeBasePage 轮询生命周期", () => {
  it("页面挂载时每秒刷新，卸载后停止", async () => {
    const { unmount } = render(<KnowledgeBasePage pollIntervalMs={20} />);
    await screen.findByText("还没有上传文档");
    await waitFor(
      () => expect(mocked.getUploads.mock.calls.length).toBeGreaterThanOrEqual(3),
      { timeout: 1000 },
    );

    unmount();
    await new Promise((resolve) => setTimeout(resolve, 60));
    const afterUnmount = mocked.getUploads.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 90));
    expect(mocked.getUploads.mock.calls.length).toBe(afterUnmount);
  });
});

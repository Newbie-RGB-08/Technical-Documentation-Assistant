import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ChatMessage, SourceItem } from "../types/chat";
import MessageBubble from "./MessageBubble";

function makeMessage(content: string, role: "user" | "assistant" = "assistant"): ChatMessage {
  return { id: "m1", role, content };
}

describe("MessageBubble 引用点击联动", () => {
  it("把 [N] 渲染为可点击的引用按钮", () => {
    render(<MessageBubble message={makeMessage("根据文档 [1] 和 [2] 可知")} />);
    expect(screen.getByRole("button", { name: "来源 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "来源 2" })).toBeInTheDocument();
  });

  it("点击 [N] 触发 onCitationClick 并传入编号", () => {
    const onCitationClick = vi.fn();
    render(
      <MessageBubble message={makeMessage("见 [3]")} onCitationClick={onCitationClick} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "来源 3" }));
    expect(onCitationClick).toHaveBeenCalledTimes(1);
    expect(onCitationClick).toHaveBeenCalledWith(3);
  });

  it("点击非引用区域不触发回调", () => {
    const onCitationClick = vi.fn();
    render(
      <MessageBubble message={makeMessage("普通文本 [1]")} onCitationClick={onCitationClick} />,
    );
    fireEvent.click(screen.getByText(/普通文本/));
    expect(onCitationClick).not.toHaveBeenCalled();
  });

  it("把模型输出的 [来源N] 也渲染为可点击按钮，且编号正确", () => {
    const onCitationClick = vi.fn();
    render(
      <MessageBubble
        message={makeMessage("这些步骤提供了一个学习路径。[来源1] - [来源10]")}
        onCitationClick={onCitationClick}
      />,
    );
    const btn = screen.getByRole("button", { name: "来源 10" });
    expect(btn).toHaveAttribute("data-citation", "10");
    // 原文形如 [来源10] 被保留为按钮文案
    expect(btn.textContent).toBe("[来源10]");
    fireEvent.click(btn);
    expect(onCitationClick).toHaveBeenCalledWith(10);
  });

  it("[来源 7]（带空格）同样可点击", () => {
    const onCitationClick = vi.fn();
    render(
      <MessageBubble message={makeMessage("见 [来源 7]")} onCitationClick={onCitationClick} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "来源 7" }));
    expect(onCitationClick).toHaveBeenCalledWith(7);
  });

  it("XSS 事件处理器被 DOMPurify 清除，[N] 仍保留为按钮", () => {
    render(
      <MessageBubble message={makeMessage('<img src=x onerror="alert(1)"> 引用 [1]')} />,
    );
    const img = document.querySelector("img");
    expect(img?.getAttribute("onerror")).toBeNull();
    expect(screen.getByRole("button", { name: "来源 1" })).toBeInTheDocument();
  });

  it("pending 时显示打字光标", () => {
    render(<MessageBubble message={{ ...makeMessage("回答中"), pending: true }} />);
    expect(screen.getByLabelText("正在生成")).toBeInTheDocument();
  });
});

describe("MessageBubble V1.4 答案状态徽章", () => {
  it("未验证通用回答显示「未验证」徽章", () => {
    render(
      <MessageBubble
        message={{ ...makeMessage("通用说明"), answer_status: "unverified_general" }}
      />,
    );
    expect(screen.getByText("未验证")).toBeInTheDocument();
  });

  it("未验证且无来源时提示“本次未使用知识库或网页来源”", () => {
    render(
      <MessageBubble
        message={{ ...makeMessage("通用说明"), answer_status: "unverified_general" }}
      />,
    );
    expect(screen.getByText("本次未使用知识库或网页来源")).toBeInTheDocument();
  });

  it("验证通过且无来源时不显示无来源提示", () => {
    render(
      <MessageBubble
        message={{ ...makeMessage("已核验内容"), answer_status: "verified" }}
      />,
    );
    expect(screen.queryByText("本次未使用知识库或网页来源")).not.toBeInTheDocument();
  });

  it("摘录兜底显示「仅摘录」徽章", () => {
    render(
      <MessageBubble message={{ ...makeMessage("摘录内容"), answer_status: "extractive" }} />,
    );
    expect(screen.getByText("仅摘录")).toBeInTheDocument();
  });

  it("中断消息显示「已中断」与重试按钮", () => {
    const onRetry = vi.fn();
    render(
      <MessageBubble
        message={{
          ...makeMessage("部分内容"),
          answer_status: "interrupted",
          interrupted: true,
        }}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByText("已中断")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("验证通过的答案不显示状态徽章", () => {
    render(
      <MessageBubble message={{ ...makeMessage("已核验内容"), answer_status: "verified" }} />,
    );
    expect(screen.queryByText("未验证")).not.toBeInTheDocument();
    expect(screen.queryByText("仅摘录")).not.toBeInTheDocument();
    expect(screen.queryByText("已中断")).not.toBeInTheDocument();
  });
});

describe("MessageBubble V1.4 内联来源列表", () => {
  const SOURCES: SourceItem[] = [
    {
      index: 1,
      title: "Chroma 入门",
      file_path: "data/notes/chroma.md",
      excerpt: "PersistentClient 使用本地目录。",
      score: 0.92,
    },
    {
      index: 2,
      title: "检索笔记",
      file_path: "data/notes/retrieval.md",
      excerpt: "RRF 融合两路检索。",
      score: 0.81,
    },
    {
      index: 3,
      title: "写回笔记",
      file_path: "data/notes/writeback.md",
      excerpt: "网页资料写回知识库。",
      score: 0.75,
    },
  ];

  it("回答下方直接展示来源卡片，无需打开抽屉", () => {
    render(
      <MessageBubble
        message={{ ...makeMessage("回答正文 [来源1]"), sources: SOURCES.slice(0, 2) }}
      />,
    );
    expect(screen.getByLabelText("来源引用")).toBeInTheDocument();
    expect(screen.getByText("来源引用（2）")).toBeInTheDocument();
    expect(screen.getByText("Chroma 入门")).toBeInTheDocument();
    expect(screen.getByText("检索笔记")).toBeInTheDocument();
  });

  it("来源超过两条时提供“查看全部”入口并触发回调", () => {
    const onOpenSources = vi.fn();
    render(
      <MessageBubble
        message={{ ...makeMessage("回答正文"), sources: SOURCES }}
        onOpenSources={onOpenSources}
      />,
    );
    expect(screen.getByText("来源引用（3）")).toBeInTheDocument();
    expect(screen.queryByText("写回笔记")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看全部 3 个来源" }));
    expect(onOpenSources).toHaveBeenCalledTimes(1);
  });

  it("两条来源时不显示“查看全部”", () => {
    render(
      <MessageBubble message={{ ...makeMessage("回答正文"), sources: SOURCES.slice(0, 2) }} />,
    );
    expect(screen.queryByRole("button", { name: /查看全部/ })).not.toBeInTheDocument();
  });

  it("用户消息不渲染来源列表", () => {
    render(
      <MessageBubble message={{ ...makeMessage("我的问题", "user"), sources: SOURCES }} />,
    );
    expect(screen.queryByLabelText("来源引用")).not.toBeInTheDocument();
  });
});

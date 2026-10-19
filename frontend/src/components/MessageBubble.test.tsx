import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "../types/chat";
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

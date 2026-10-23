import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

vi.mock("./components/ChatContainer", () => ({
  default: () => <div>聊天页面</div>,
}));

vi.mock("./components/KnowledgeBasePage", () => ({
  default: () => <div>知识库页面</div>,
}));

function navigate(hash: string) {
  act(() => {
    window.history.replaceState(null, "", hash);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
}

describe("App 轻量 hash 路由", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "#/chat");
  });

  it("#/chat 与默认入口渲染聊天页", () => {
    render(<App />);
    expect(screen.getByText("聊天页面")).toBeInTheDocument();
  });

  it("#/knowledge 直接渲染知识库页", () => {
    window.history.replaceState(null, "", "#/knowledge");
    render(<App />);
    expect(screen.getByText("知识库页面")).toBeInTheDocument();
  });

  it("hashchange 在聊天页与知识库页之间切换", () => {
    render(<App />);
    expect(screen.getByText("聊天页面")).toBeInTheDocument();

    navigate("#/knowledge");
    expect(screen.getByText("知识库页面")).toBeInTheDocument();

    navigate("#/chat");
    expect(screen.getByText("聊天页面")).toBeInTheDocument();
  });

  it("未知 hash 回退 #/chat", () => {
    window.history.replaceState(null, "", "#/unknown");
    render(<App />);
    expect(screen.getByText("聊天页面")).toBeInTheDocument();
    expect(window.location.hash).toBe("#/chat");
  });

  it("空 hash 规范化为 #/chat，旧聊天页不回归", () => {
    window.history.replaceState(null, "", "/");
    render(<App />);
    expect(screen.getByText("聊天页面")).toBeInTheDocument();
    expect(window.location.hash).toBe("#/chat");
  });
});

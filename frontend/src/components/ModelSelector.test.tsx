/**
 * V1.5.x：ModelSelector 分组渲染（本地/云端 optgroup + 参数量后缀 + 未装机提示）。
 * 组件为受控展示，不发请求。
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import ModelSelector from "./ModelSelector";
import type { ModelOption } from "../types/chat";

const MODELS: ModelOption[] = [
  {
    id: "ollama:qwen2.5:7b", name: "qwen2.5:7b", provider: "ollama",
    type: "local", available: true, source: "config", parameter_size: 7.6,
  },
  {
    id: "ollama:llama3.1:8b", name: "llama3.1:8b", provider: "ollama",
    type: "local", available: true, source: "discovered", parameter_size: 8,
  },
  {
    id: "ollama:qwen2.5:3b", name: "qwen2.5:3b", provider: "ollama",
    type: "local", available: false, source: "config", missing_models: ["qwen2.5:3b"],
  },
  {
    id: "deepseek:deepseek-chat", name: "deepseek-chat", provider: "deepseek",
    type: "cloud", available: true, source: "config",
  },
];

describe("ModelSelector", () => {
  it("按本地/云端分组，本地项带参数量后缀", () => {
    const { container } = render(
      <ModelSelector value={null} onChange={vi.fn()} models={MODELS} loaded />,
    );
    const groups = Array.from(container.querySelectorAll("optgroup"));
    expect(groups.map((g) => g.getAttribute("label"))).toEqual(["本地", "云端"]);
    expect(groups[0].querySelectorAll("option")).toHaveLength(3);
    expect(groups[1].querySelectorAll("option")).toHaveLength(1);

    expect(screen.getByText("qwen2.5:7b · 7.6B")).toBeInTheDocument();
    expect(screen.getByText("llama3.1:8b · 8B")).toBeInTheDocument();
    // 云端项不带参数量
    expect(screen.getByText("deepseek-chat")).toBeInTheDocument();
  });

  it("配置声明但未安装的本地项标注（未安装）且不可选", () => {
    render(<ModelSelector value={null} onChange={vi.fn()} models={MODELS} loaded />);
    const missing = screen.getByText("qwen2.5:3b（未安装）") as HTMLOptionElement;
    expect(missing.disabled).toBe(true);
  });

  it("提供“自动（按可用性选择）”默认项与无模型占位", () => {
    const { container, rerender } = render(
      <ModelSelector value={null} onChange={vi.fn()} models={MODELS} loaded />,
    );
    expect(screen.getByText("自动（按可用性选择）")).toBeInTheDocument();

    rerender(<ModelSelector value={null} onChange={vi.fn()} models={[]} loaded />);
    expect(screen.getByText("无可用模型")).toBeInTheDocument();
    expect((container.querySelector("select") as HTMLSelectElement).disabled).toBe(true);
  });
});

/**
 * V1.5.x：Provider 状态上提 hook 与横幅推导。
 * - error：清单全部不可用（含 detail 诊断行）
 * - notice：本地不可用但云端可用
 * - refresh() 携带 force-refresh 语义
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { act } from "react";
import { useProviderStatus, deriveProviderBanner } from "./useProviderStatus";
import { getModels, getProviderStatus } from "../api/client";
import type { ModelOption, ProviderStatusReport } from "../types/chat";

vi.mock("../api/client", () => ({
  getModels: vi.fn(),
  getProviderStatus: vi.fn(),
  streamChat: vi.fn(),
  postFeedback: vi.fn(),
}));

const LOCAL_OK: ModelOption = {
  id: "ollama:qwen2.5:7b", name: "qwen2.5:7b", provider: "ollama",
  type: "local", available: true, source: "config",
};
const LOCAL_DOWN: ModelOption = {
  id: "ollama:gone", name: "gone", provider: "ollama",
  type: "local", available: false,
};
const CLOUD_OK: ModelOption = {
  id: "deepseek:deepseek-chat", name: "deepseek-chat", provider: "deepseek",
  type: "cloud", available: true, source: "config",
};

function report(providers: Array<{ name: string; available: boolean; reason: string }>): ProviderStatusReport {
  return {
    enabled: true,
    providers: providers.map((p) => ({
      name: p.name, type: "local" as const, available: p.available,
      reason: p.reason, detail: "", installed_models: [], selected_for: [],
    })),
    bindings: {},
  };
}

describe("useProviderStatus", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.mocked(getModels).mockReset();
    vi.mocked(getProviderStatus).mockReset();
  });

  it("并行加载清单与状态，并自动选中首个可用模型", async () => {
    const onChange = vi.fn();
    vi.mocked(getModels).mockResolvedValue([LOCAL_OK, CLOUD_OK]);
    vi.mocked(getProviderStatus).mockResolvedValue(
      report([{ name: "ollama", available: true, reason: "ok" }]),
    );

    const { result } = renderHook(() => useProviderStatus(null, onChange));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.models).toHaveLength(2);
    expect(result.current.status?.providers[0].name).toBe("ollama");
    expect(onChange).toHaveBeenCalledWith("ollama:qwen2.5:7b");
    expect(result.current.banner).toBeNull();
    expect(getModels).toHaveBeenCalledWith(false);
    expect(getProviderStatus).toHaveBeenCalledWith(false);
  });

  it("全部模型不可用 → error 横幅（含诊断明细）", async () => {
    const onChange = vi.fn();
    vi.mocked(getModels).mockResolvedValue([LOCAL_DOWN]);
    vi.mocked(getProviderStatus).mockResolvedValue(
      report([{ name: "ollama", available: false, reason: "service_unreachable" }]),
    );

    const { result } = renderHook(() => useProviderStatus(null, onChange));
    await waitFor(() => expect(result.current.banner).not.toBeNull());
    expect(result.current.banner?.kind).toBe("error");
    expect(result.current.banner?.text).toContain("未检测到可用大模型");
    expect((result.current.banner as { detail?: string }).detail).toContain(
      "ollama：service_unreachable",
    );
  });

  it("本地不可用但云端可用 → notice 横幅", async () => {
    const onChange = vi.fn();
    vi.mocked(getModels).mockResolvedValue([LOCAL_DOWN, CLOUD_OK]);
    vi.mocked(getProviderStatus).mockResolvedValue(
      report([{ name: "ollama", available: false, reason: "service_unreachable" }]),
    );

    const { result } = renderHook(() => useProviderStatus(null, onChange));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.banner?.kind).toBe("notice");
    expect(result.current.banner?.text).toContain("在线模型");
  });

  it("refresh() 携带刷新语义重新拉取", async () => {
    const onChange = vi.fn();
    vi.mocked(getModels).mockResolvedValue([LOCAL_OK]);
    vi.mocked(getProviderStatus).mockResolvedValue(
      report([{ name: "ollama", available: true, reason: "ok" }]),
    );
    const { result } = renderHook(() => useProviderStatus("ollama:qwen2.5:7b", onChange));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    // 已有初值时不自动改选
    expect(onChange).not.toHaveBeenCalled();

    await act(async () => {
      await result.current.refresh();
    });
    expect(getModels).toHaveBeenLastCalledWith(true);
    expect(getProviderStatus).toHaveBeenLastCalledWith(true);
  });

  it("清单请求失败也置 loaded，表现为 error 引导", async () => {
    const onChange = vi.fn();
    vi.mocked(getModels).mockRejectedValue(new Error("boom"));
    vi.mocked(getProviderStatus).mockRejectedValue(new Error("boom"));

    const { result } = renderHook(() => useProviderStatus(null, onChange));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.banner?.kind).toBe("error");
  });
});

describe("deriveProviderBanner（纯函数）", () => {
  it("未加载时不出横幅", () => {
    expect(deriveProviderBanner([], null, false)).toBeNull();
  });
});

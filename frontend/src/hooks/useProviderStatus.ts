/**
 * V1.5.x：Provider 健康状态上提 hook（方案 v2 / D8）。
 *
 * - 并行拉取联合模型清单 + Provider 状态快照（TTL 内零额外网络压力）
 * - 推导顶部横幅：全部不可用 → error；本地不可用但云端可用 → notice
 * - refresh()：携带 refresh=true 强制重探（done 后或用户手动触发）
 * - 首次加载成功后自动选中第一个可用模型（保持旧 ModelSelector 行为）
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getModels, getProviderStatus } from "../api/client";
import type { ModelOption, ProviderStatusReport } from "../types/chat";

export type ProviderBanner =
  | { kind: "error"; text: string; detail?: string }
  | { kind: "notice"; text: string }
  | null;

/** 纯函数：由联合清单 + 状态快照推导横幅（可单测）。文案不点名具体型号。 */
export function deriveProviderBanner(
  models: ModelOption[],
  status: ProviderStatusReport | null,
  loaded: boolean,
): ProviderBanner {
  if (!loaded) return null;
  const localAvailable = models.some((m) => m.type === "local" && m.available);
  const cloudAvailable = models.some((m) => m.type !== "local" && m.available);
  const anyAvailable = models.some((m) => m.available);
  if (!anyAvailable) {
    const lines = (status?.providers ?? [])
      .filter((p) => !p.available)
      .map((p) => `${p.name}：${p.reason}`);
    return {
      kind: "error",
      text:
        "未检测到可用大模型。请启动 Ollama 并安装任一对话模型，或在 backend/.env 配置在线模型 Key，然后点击右下角“刷新”重试。",
      detail: lines.length > 0 ? lines.join("\n") : undefined,
    };
  }
  if (!localAvailable && cloudAvailable) {
    return {
      kind: "notice",
      text: "本地 Ollama 未检测到可用对话模型，已自动使用在线模型回答。",
    };
  }
  return null;
}

export function useProviderStatus(
  modelId: string | null,
  onChangeModel: (id: string) => void,
) {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [status, setStatus] = useState<ProviderStatusReport | null>(null);
  const [loaded, setLoaded] = useState(false);
  const autoPickedRef = useRef(false);
  const modelIdRef = useRef<string | null>(modelId);
  modelIdRef.current = modelId;

  const load = useCallback(async (refresh: boolean): Promise<ModelOption[]> => {
    const [itemsResult, statusResult] = await Promise.allSettled([
      getModels(refresh),
      getProviderStatus(refresh),
    ]);
    if (itemsResult.status === "fulfilled") setModels(itemsResult.value);
    if (statusResult.status === "fulfilled") setStatus(statusResult.value);
    setLoaded(true); // 即使失败也放行（空清单 → error 横幅引导）
    return itemsResult.status === "fulfilled" ? itemsResult.value : [];
  }, []);

  useEffect(() => {
    let cancelled = false;
    void load(false).then((items) => {
      if (cancelled || autoPickedRef.current) return;
      autoPickedRef.current = true;
      const firstAvailable = items.find((m) => m.available);
      if (firstAvailable && !modelIdRef.current) onChangeModel(firstAvailable.id);
    });
    return () => {
      cancelled = true;
    };
  }, [load, onChangeModel]);

  const refresh = useCallback(() => load(true), [load]);

  const banner = useMemo(
    () => deriveProviderBanner(models, status, loaded),
    [models, status, loaded],
  );

  return { models, status, loaded, banner, refresh };
}

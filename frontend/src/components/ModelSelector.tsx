import { useEffect, useState } from "react";
import { getModels } from "../api/client";
import type { ModelOption } from "../types/chat";

interface Props {
  value: string | null;
  onChange: (modelId: string | null) => void;
}

export default function ModelSelector({ value, onChange }: Props) {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getModels()
      .then((items) => {
        if (cancelled) return;
        setModels(items);
        const firstAvailable = items.find((m) => m.available);
        if (!value && firstAvailable) onChange(firstAvailable.id);
      })
      .catch(() => undefined)
      .finally(() => !cancelled && setLoaded(true));
    return () => {
      cancelled = true;
    };
  }, [onChange, value]);

  return (
    <label className="model-selector glass">
      <span>模型</span>
      <select
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value || null)}
        disabled={!loaded || models.length === 0}
      >
        {!loaded && <option value="">加载中…</option>}
        {loaded && models.length === 0 && <option value="">无可用模型</option>}
        {models.map((model) => (
          <option key={model.id} value={model.id} disabled={!model.available}>
            {model.name} · {model.provider === "ollama" ? "本地" : "云端"}
            {model.available ? "" : "（不可用）"}
          </option>
        ))}
      </select>
    </label>
  );
}

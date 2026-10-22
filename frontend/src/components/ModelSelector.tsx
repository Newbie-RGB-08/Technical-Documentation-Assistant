import type { ModelOption } from "../types/chat";

interface Props {
  value: string | null;
  onChange: (modelId: string | null) => void;
  /** V1.5.x：清单由 useProviderStatus 上提提供，组件自身不再发请求 */
  models: ModelOption[];
  loaded: boolean;
}

function formatSize(size: number | null | undefined): string {
  if (typeof size !== "number" || Number.isNaN(size) || size <= 0) return "";
  return ` · ${size % 1 === 0 ? String(size) : size.toFixed(1)}B`;
}

function optionLabel(model: ModelOption): string {
  const segments = [model.name, formatSize(model.parameter_size)];
  if (!model.available) {
    // 配置声明但未安装 → 区别于一般不可用
    const missing = Array.isArray(model.missing_models) && model.missing_models.includes(model.name);
    segments.push(missing ? "（未安装）" : "（不可用）");
  }
  return segments.filter(Boolean).join("");
}

export default function ModelSelector({ value, onChange, models, loaded }: Props) {
  const locals = models.filter((m) => m.type === "local");
  const clouds = models.filter((m) => m.type !== "local");

  const renderGroup = (label: string, items: ModelOption[], key: string) =>
    items.length === 0 ? null : (
      <optgroup key={key} label={label}>
        {items.map((model) => (
          <option key={model.id} value={model.id} disabled={!model.available}>
            {optionLabel(model)}
          </option>
        ))}
      </optgroup>
    );

  return (
    <label className="model-selector glass">
      <span>模型</span>
      <select
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value || null)}
        disabled={!loaded || models.length === 0}
      >
        <option value="">自动（按可用性选择）</option>
        {!loaded && models.length === 0 && <option value="" disabled>加载中…</option>}
        {loaded && models.length === 0 && <option value="" disabled>无可用模型</option>}
        {renderGroup("本地", locals, "local")}
        {renderGroup("云端", clouds, "cloud")}
      </select>
    </label>
  );
}

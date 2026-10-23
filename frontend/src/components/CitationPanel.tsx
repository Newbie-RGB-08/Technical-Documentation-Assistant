import type { SourceItem } from "../types/chat";
import SourceCard from "./SourceCard";

interface Props {
  sources: SourceItem[];
  activeIndex: number | null;
  open: boolean;
  onClose: () => void;
  onSelect: (index: number) => void;
  /** V1.4：当前回答状态透传到来源面板 */
  answerStatus?: string | null;
  /** 上传文档预览入口；旧调用不传时由 SourceCard 回退。 */
  onOpenUpload?: (uploadId: string) => void;
}

const STATUS_LABELS: Record<string, string> = {
  verified: "已验证",
  extractive: "仅摘录",
  partial: "部分覆盖",
  unverified_general: "未验证",
  unverified_general_flagged: "未验证",
  interrupted: "已中断",
  refused: "未找到",
};

export default function CitationPanel({
  sources,
  activeIndex,
  open,
  onClose,
  onSelect,
  answerStatus,
  onOpenUpload,
}: Props) {
  // V1.2：计数改为双口径（篇文档 / 个片段），并且引用高亮按片段序号集合判定
  const chunkTotal = sources.reduce(
    (sum, source) => sum + (source.chunk_count ?? source.indices?.length ?? 1),
    0,
  );
  const isActive = (source: SourceItem) =>
    source.indices?.length
      ? activeIndex !== null && source.indices.includes(activeIndex)
      : activeIndex === source.index;

  return (
    <aside className={`citation-panel glass ${open ? "open" : ""}`} aria-hidden={!open}>
      <div className="citation-panel-header">
        <div>
          <div className="panel-title">
            来源引用
            {answerStatus && STATUS_LABELS[answerStatus] && (
              <span className={`panel-status-badge ${answerStatus}`}>
                {STATUS_LABELS[answerStatus]}
              </span>
            )}
          </div>
          <div className="panel-subtitle">
            使用了 {sources.length} 篇文档 / {chunkTotal} 个片段
          </div>
        </div>
        <button type="button" className="icon-button" onClick={onClose} aria-label="关闭来源面板">
          ×
        </button>
      </div>
      <div className="citation-list">
        {sources.map((source) => (
          <SourceCard
            key={`${source.source_type ?? ""}|${source.file_path}|${source.index}`}
            source={source}
            active={isActive(source)}
            onClick={() => onSelect(source.index)}
            onOpenUpload={onOpenUpload}
          />
        ))}
      </div>
    </aside>
  );
}

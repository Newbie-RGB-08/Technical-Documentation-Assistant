import type { SourceItem } from "../types/chat";
import SourceCard from "./SourceCard";

interface Props {
  sources: SourceItem[];
  activeIndex: number | null;
  open: boolean;
  onClose: () => void;
  onSelect: (index: number) => void;
}

export default function CitationPanel({ sources, activeIndex, open, onClose, onSelect }: Props) {
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
          <div className="panel-title">来源引用</div>
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
          />
        ))}
      </div>
    </aside>
  );
}

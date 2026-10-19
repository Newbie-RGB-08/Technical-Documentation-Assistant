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
  return (
    <aside className={`citation-panel glass ${open ? "open" : ""}`} aria-hidden={!open}>
      <div className="citation-panel-header">
        <div>
          <div className="panel-title">来源引用</div>
          <div className="panel-subtitle">使用了 {sources.length} 个来源</div>
        </div>
        <button type="button" className="icon-button" onClick={onClose} aria-label="关闭来源面板">
          ×
        </button>
      </div>
      <div className="citation-list">
        {sources.map((source) => (
          <SourceCard
            key={`${source.index}-${source.file_path}`}
            source={source}
            active={activeIndex === source.index}
            onClick={() => onSelect(source.index)}
          />
        ))}
      </div>
    </aside>
  );
}

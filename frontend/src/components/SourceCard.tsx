import type { SourceItem } from "../types/chat";

interface Props {
  source: SourceItem;
  active?: boolean;
  onClick?: () => void;
}

export default function SourceCard({ source, active, onClick }: Props) {
  return (
    <button
      type="button"
      className={`source-card glass ${active ? "active" : ""}`}
      onClick={onClick}
    >
      <div className="source-card-header">
        <span className="source-index">[{source.index}]</span>
        <span className="source-score">{source.score.toFixed(3)}</span>
      </div>
      <div className="source-path">{source.file_path}</div>
      {source.heading_path && <div className="source-heading">{source.heading_path}</div>}
      <p className="source-excerpt">{source.excerpt}</p>
    </button>
  );
}

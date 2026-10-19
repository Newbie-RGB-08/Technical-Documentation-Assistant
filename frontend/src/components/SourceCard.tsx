import { useCallback } from "react";
import type { SourceItem } from "../types/chat";

interface Props {
  source: SourceItem;
  active?: boolean;
  onClick?: () => void;
}

/** V1.1：来源卡片 —— 点击后按 source_type / source_url 分支处理。
 *  - tech_doc + source_url → 新标签打开网页
 *  - personal_note / 兜底 → 调后端 /api/files/open 用默认应用打开 md
 */
export default function SourceCard({ source, active, onClick }: Props) {
  const handleClick = useCallback(() => {
    if (onClick) onClick(); // 保留父组件的 CitationPanel 激活逻辑

    if (source.source_type === "tech_doc" && source.source_url) {
      window.open(source.source_url, "_blank", "noopener,noreferrer");
      return;
    }
    // 本地 md 或兜底
    fetch(`/api/files/open?path=${encodeURIComponent(source.file_path)}`).catch(
      () => { /* 后端没起来 / 路径非法时静默忽略 */ }
    );
  }, [source, onClick]);

  const displayTitle =
    source.title?.trim() || source.file_path.split(/[\\/]/).pop() || "未命名来源";
  const isWebSource = source.source_type === "tech_doc" && !!source.source_url;
  const sourceKindLabel = source.source_type === "tech_doc" ? "网页文档" : "本地笔记";

  return (
    <button
      type="button"
      className={`source-card glass ${active ? "active" : ""}`}
      onClick={handleClick}
      title={isWebSource ? `点击跳转：${source.source_url}` : `点击打开文件：${source.file_path}`}
    >
      <div className="source-card-header">
        <span className="source-index">[{source.index}]</span>
        <span className={`source-kind-badge ${isWebSource ? "web" : "local"}`}>
          {sourceKindLabel}
        </span>
        <span className="source-score">{source.score.toFixed(3)}</span>
      </div>
      <div className="source-title">{displayTitle}</div>
      <div className="source-sub">
        {isWebSource ? (
          <span className="source-link-hint">
            ↗ 点击跳转原始网页
          </span>
        ) : (
          <span className="source-link-hint">
            📄 点击在本地打开
          </span>
        )}
      </div>
      <div className="source-path">{source.file_path}</div>
      {source.heading_path && (
        <div className="source-heading">章节：{source.heading_path}</div>
      )}
      <p className="source-excerpt">{source.excerpt}</p>
    </button>
  );
}

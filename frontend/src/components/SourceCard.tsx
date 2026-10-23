import { useCallback, useMemo, useState } from "react";
import type { SourceItem } from "../types/chat";

/** 折叠状态下默认展示的摘要条数（V1.2） */
const COLLAPSED_EXCERPTS = 2;

interface Props {
  source: SourceItem;
  active?: boolean;
  onClick?: () => void;
  /** 知识库上传文档预览入口；未提供时回退打开 Markdown。 */
  onOpenUpload?: (uploadId: string) => void;
}

/** 来源卡片。
 *  V1.1：tech_doc+url → 网页；personal_note/兜底 → 本地打开。
 *  V1.2：同文档多片段合并展示（共 N 段 / 多摘要可展开）。
 *  V1.3：
 *   - 隐藏明文路径与 URL（只显示文件名，不渲染完整地址）；
 *   - 不再渲染章节列表（与标题/摘要重复）；
 *   - hybrid（本地+网络同文档）卡提供「本地打开」与「网页原文」两个入口。
 */
export default function SourceCard({ source, active, onClick, onOpenUpload }: Props) {
  const [expanded, setExpanded] = useState(false);

  const isUploadSource = source.source_type === "uploaded_doc";
  const uploadId = source.upload_id ?? "";

  const openLocal = useCallback(() => {
    fetch(`/api/files/open?path=${encodeURIComponent(source.file_path)}`).catch(
      () => { /* 后端没起来 / 路径非法时静默忽略 */ }
    );
  }, [source.file_path]);

  const openWeb = useCallback(() => {
    if (source.source_url) {
      window.open(source.source_url, "_blank", "noopener,noreferrer");
    }
  }, [source.source_url]);

  const openUpload = useCallback(() => {
    if (!uploadId) return;
    if (onOpenUpload) {
      onOpenUpload(uploadId);
      return;
    }
    window.open(
      `/api/uploads/${encodeURIComponent(uploadId)}/markdown`,
      "_blank",
      "noopener,noreferrer",
    );
  }, [onOpenUpload, uploadId]);

  const isHybrid = source.is_hybrid === true;
  const isWebSource = source.source_type === "tech_doc" && !!source.source_url;

  const handleCardClick = useCallback(() => {
    if (onClick) onClick(); // 保留父组件的 CitationPanel 激活逻辑
    if (isHybrid) return; // hybrid 卡用显式按钮，卡片整体点击不触发跳转
    if (isUploadSource) {
      openUpload();
      return;
    }
    if (isWebSource) openWeb();
    else openLocal();
  }, [
    onClick,
    isHybrid,
    isUploadSource,
    openUpload,
    isWebSource,
    openWeb,
    openLocal,
  ]);

  const indices = useMemo(
    () => (source.indices?.length ? source.indices : [source.index]),
    [source.indices, source.index],
  );
  const excerpts = useMemo(
    () => (source.excerpts?.length ? source.excerpts : source.excerpt ? [source.excerpt] : []),
    [source.excerpts, source.excerpt],
  );

  const chunkCount = source.chunk_count ?? indices.length;
  const hiddenCount = Math.max(excerpts.length - COLLAPSED_EXCERPTS, 0);
  const visibleExcerpts = expanded ? excerpts : excerpts.slice(0, COLLAPSED_EXCERPTS);

  // 上传文档优先展示原始文件名（含扩展名）；旧来源保持 title/路径文件名逻辑。
  const displayTitle = isUploadSource
    ? source.original_filename?.trim() ||
      source.title?.trim() ||
      source.file_path.split(/[\\/]/).pop() ||
      "未命名来源"
    : source.title?.trim() || source.file_path.split(/[\\/]/).pop() || "未命名来源";
  // V1.3：只显示文件名，不暴露完整路径
  const basename = isUploadSource
    ? source.original_filename?.trim() ||
      source.file_path.split(/[\\/]/).pop() ||
      source.file_path
    : source.file_path.split(/[\\/]/).pop() || source.file_path;
  const sourceKindLabel = isUploadSource
    ? "上传文档"
    : source.source_type === "tech_doc"
      ? "网页文档"
      : "本地笔记";

  // V1.4：联网来源徽章与抓取时间（旧数据按类型推导）
  const origin = source.origin ?? (isHybrid ? "hybrid" : isWebSource ? "web_search" : "local");
  const isOnline = origin === "web_search" || origin === "hybrid";
  const fetchedDate = (source.retrieved_at ?? "").match(/\d{4}-\d{2}-\d{2}/)?.[0] ?? "";

  return (
    <div
      role="button"
      tabIndex={0}
      className={`source-card glass ${active ? "active" : ""} ${source.used === false ? "unused" : ""}`}
      onClick={handleCardClick}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          handleCardClick();
        }
      }}
      title={
        isUploadSource
          ? "点击预览解析结果"
          : isHybrid
            ? "本地笔记 + 网络文章"
            : isWebSource
              ? "点击跳转原始网页"
              : "点击在本地打开"
      }
    >
      <div className="source-card-header">
        <span className="source-index">[{source.index}]</span>
        {chunkCount > 1 && (
          <span className="source-chunk-badge">共 {chunkCount} 段</span>
        )}
        <span className={`source-kind-badge ${isWebSource && !isHybrid ? "web" : "local"}`}>
          {sourceKindLabel}
        </span>
        {isOnline && <span className="source-origin-badge web">联网检索</span>}
        {source.used === false && (
          <span className="source-unused-badge">未引用</span>
        )}
        {fetchedDate && (
          <span className="source-fetched-at">抓取于 {fetchedDate}</span>
        )}
        <span className="source-score">{source.score.toFixed(3)}</span>
      </div>
      <div className="source-title">{displayTitle}</div>
      <div className="source-actions">
        {isHybrid ? (
          <>
            <button
              type="button"
              className="source-action-btn primary"
              onClick={(event) => {
                event.stopPropagation();
                openLocal();
              }}
            >
              📄 本地打开
            </button>
            <button
              type="button"
              className="source-action-btn"
              onClick={(event) => {
                event.stopPropagation();
                openWeb();
              }}
            >
              ↗ 网页原文
            </button>
          </>
        ) : (
          <span className="source-link-hint">
            {isUploadSource
              ? "📄 点击预览解析结果"
              : isWebSource
                ? "↗ 点击跳转原始网页"
                : "📄 点击在本地打开"}
          </span>
        )}
      </div>
      <div className="source-path" data-path={source.file_path}>{basename}</div>
      <div className="source-excerpt-list">
        {visibleExcerpts.map((text, i) => (
          <p key={`${i}-${text.slice(0, 16)}`} className="source-excerpt">
            {text}
          </p>
        ))}
      </div>
      {hiddenCount > 0 && (
        <button
          type="button"
          className="source-more"
          onClick={(event) => {
            event.stopPropagation();
            setExpanded((prev) => !prev);
          }}
        >
          {expanded ? "收起" : `展开剩余 ${hiddenCount} 段`}
        </button>
      )}
    </div>
  );
}

import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/common";
import "highlight.js/styles/github-dark.css";
import { marked } from "marked";
import { useEffect, useMemo, useRef } from "react";
import type { ChatMessage, SourceItem } from "../types/chat";
import SourceCard from "./SourceCard";

function renderMarkdown(content: string): string {
  const raw = marked.parse(content, { async: false }) as string;
  const clean = DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
  // 同时识别 [N] 与模型按提示词输出的 [来源N] / [来源 N]（保留原文作为按钮文案）
  return clean.replace(
    /\[(?:来源\s*)?(\d{1,2})\]/g,
    '<button type="button" class="citation-link" data-citation="$1" aria-label="来源 $1">$&</button>',
  );
}

interface Props {
  message: ChatMessage;
  onCitationClick?: (index: number) => void;
  onRetry?: () => void;
  /** V1.4 优化：内联来源的“查看全部”入口 */
  onOpenSources?: () => void;
  /** 上传文档来源预览入口；旧调用不传时由 SourceCard 回退。 */
  onOpenUpload?: (uploadId: string) => void;
}

/** 无证据类回答：没有任何来源时给出明确提示。 */
const NO_SOURCE_STATUSES = new Set([
  "unverified_general",
  "unverified_general_flagged",
  "refused",
  "interrupted",
  "error",
]);

/** 回答气泡内联展示的来源条数（其余进“查看全部”）。 */
const INLINE_SOURCE_LIMIT = 2;

/** V1.4：答案状态徽章（未验证 / 仅摘录 / 已中断）。 */
const STATUS_BADGES: Record<string, { label: string; tone: string }> = {
  unverified_general: { label: "未验证", tone: "warn" },
  unverified_general_flagged: { label: "未验证", tone: "warn" },
  extractive: { label: "仅摘录", tone: "info" },
  interrupted: { label: "已中断", tone: "muted" },
};

export default function MessageBubble({
  message,
  onCitationClick,
  onRetry,
  onOpenSources,
  onOpenUpload,
}: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const html = useMemo(() => renderMarkdown(message.content || ""), [message.content]);
  const sources: SourceItem[] = message.role === "assistant" ? message.sources ?? [] : [];
  const inlineSources = sources.slice(0, INLINE_SOURCE_LIMIT);
  const hiddenSourceCount = Math.max(sources.length - inlineSources.length, 0);

  useEffect(() => {
    rootRef.current
      ?.querySelectorAll("pre code")
      .forEach((el) => hljs.highlightElement(el as HTMLElement));
  }, [html]);

  const badge = message.interrupted
    ? STATUS_BADGES.interrupted
    : STATUS_BADGES[message.answer_status ?? ""];

  const showNoSourceHint =
    message.role === "assistant" &&
    !message.pending &&
    sources.length === 0 &&
    NO_SOURCE_STATUSES.has(message.answer_status ?? "");

  const handleClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const target = (event.target as HTMLElement).closest("[data-citation]");
    if (!target) return;
    const index = Number(target.getAttribute("data-citation"));
    if (Number.isFinite(index)) onCitationClick?.(index);
  };

  return (
    <article className={`message-row ${message.role}`}>
      <div className={`message-bubble glass ${message.role}`}>
        <div className="message-role">
          <span>{message.role === "user" ? "你" : "助手"}</span>
          {badge && (
            <span
              className={`message-status-badge ${badge.tone}`}
              data-answer-status={message.answer_status ?? ""}
            >
              {badge.label}
            </span>
          )}
        </div>
        <div
          ref={rootRef}
          className="markdown-body"
          onClick={handleClick}
          dangerouslySetInnerHTML={{ __html: html }}
        />
        {message.pending && <span className="typing-cursor" aria-label="正在生成" />}
        {showNoSourceHint && (
          <div className="message-no-source">本次未使用知识库或网页来源</div>
        )}
        {sources.length > 0 && (
          <section className="inline-sources" aria-label="来源引用">
            <div className="inline-sources-header">
              <span className="inline-sources-title">来源引用（{sources.length}）</span>
              {hiddenSourceCount > 0 && onOpenSources && (
                <button type="button" className="inline-sources-more" onClick={onOpenSources}>
                  查看全部 {sources.length} 个来源
                </button>
              )}
            </div>
            <div className="inline-source-list">
              {inlineSources.map((source) => (
                <SourceCard
                  key={`${source.source_type ?? ""}|${source.file_path}|${source.index}`}
                  source={source}
                  onOpenUpload={onOpenUpload}
                />
              ))}
            </div>
          </section>
        )}
        {message.error && <div className="message-error">{message.error}</div>}
        {message.interrupted && onRetry && (
          <button type="button" className="message-retry" onClick={onRetry}>
            重试
          </button>
        )}
      </div>
    </article>
  );
}

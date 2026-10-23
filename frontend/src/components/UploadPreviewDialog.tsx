import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useMemo, useState } from "react";
import { getUploadMarkdown } from "../api/client";

export interface UploadPreviewDialogProps {
  uploadId: string | null;
  onClose: () => void;
}

/** 把解析后的相对资源链接重写到上传资源接口（后端通常已重写，这里再做一次前端兜底）。 */
export function rewriteUploadAssets(markdown: string, uploadId: string): string {
  const base = `/api/uploads/${encodeURIComponent(uploadId)}/assets/`;
  return markdown
    .replace(/\]\((?:\.\/)?assets\//g, `](${base}`)
    .replace(/(src=["'])(?:\.\/)?assets\//g, `$1${base}`);
}

export default function UploadPreviewDialog({ uploadId, onClose }: UploadPreviewDialogProps) {
  const [markdown, setMarkdown] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!uploadId) {
      setMarkdown("");
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setMarkdown("");
    setError(null);
    void getUploadMarkdown(uploadId, controller.signal)
      .then((text) => {
        if (!controller.signal.aborted) setMarkdown(text);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [uploadId]);

  useEffect(() => {
    if (!uploadId) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [uploadId, onClose]);

  const html = useMemo(() => {
    if (!markdown) return "";
    const raw = marked.parse(rewriteUploadAssets(markdown, uploadId ?? ""), {
      async: false,
    }) as string;
    return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
  }, [markdown, uploadId]);

  if (!uploadId) return null;

  return (
    <div
      className="upload-preview-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="upload-preview-dialog glass"
        role="dialog"
        aria-modal="true"
        aria-label="上传文档预览"
      >
        <header className="upload-preview-header">
          <div>
            <div className="upload-preview-title">上传文档预览</div>
            <div className="upload-preview-subtitle">{uploadId}</div>
          </div>
          <div className="upload-preview-actions">
            <a
              className="ghost-button upload-original-link"
              href={`/api/uploads/${encodeURIComponent(uploadId)}/original`}
              target="_blank"
              rel="noopener noreferrer"
            >
              打开原文件
            </a>
            <button
              type="button"
              className="icon-button"
              onClick={onClose}
              aria-label="关闭预览"
            >
              ×
            </button>
          </div>
        </header>
        <div className="upload-preview-body">
          {loading && <div className="upload-preview-loading">正在加载解析结果…</div>}
          {error && (
            <div className="kb-error" role="alert">
              <strong>预览失败</strong>
              <span className="kb-error-detail">{error}</span>
            </div>
          )}
          {!loading && !error && html && (
            <div className="markdown-body" dangerouslySetInnerHTML={{ __html: html }} />
          )}
          {!loading && !error && !html && (
            <div className="upload-preview-empty">暂无解析内容</div>
          )}
        </div>
      </section>
    </div>
  );
}

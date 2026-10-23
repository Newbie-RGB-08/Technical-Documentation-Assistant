import type { UploadFile } from "../types/upload";
import {
  engineLabel,
  formatFileSize,
  isBusyStatus,
  uploadStatusLabel,
  uploadStatusTone,
} from "../utils/upload";

interface Props {
  items: UploadFile[];
  loading: boolean;
  actionBusy: string | null;
  onPreview: (uploadId: string) => void;
  onReparse: (uploadId: string) => void;
  onReindex: (uploadId: string) => void;
  onDelete: (uploadId: string) => void;
}

function basename(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

export default function UploadList({
  items,
  loading,
  actionBusy,
  onPreview,
  onReparse,
  onReindex,
  onDelete,
}: Props) {
  if (loading && items.length === 0) {
    return <div className="upload-list-empty glass">正在加载文档列表…</div>;
  }
  if (items.length === 0) {
    return (
      <div className="upload-list-empty glass">
        <strong>还没有上传文档</strong>
        <span>切到“上传”页签，添加 PDF、Word、Markdown 等文件。</span>
      </div>
    );
  }

  return (
    <div className="upload-list" role="table" aria-label="上传文档列表">
      <div className="upload-list-header" role="row">
        <span role="columnheader">文件</span>
        <span role="columnheader">状态</span>
        <span role="columnheader">格式 / 大小</span>
        <span role="columnheader">块数 / 引擎</span>
        <span role="columnheader">操作</span>
      </div>
      {items.map((item) => {
        const busy = actionBusy === item.upload_id || isBusyStatus(item.status);
        const failed = item.status === "failed_parse" || item.status === "failed_index";
        const canPreview =
          item.status === "parsed" || item.status === "indexed" || !!item.markdown_path;
        const filename = item.original_filename || basename(item.stored_path || item.upload_id);
        return (
          <div className="upload-row" role="row" key={item.upload_id} data-status={item.status}>
            <div className="upload-cell upload-file-cell" role="cell">
              <span className="upload-filename" title={filename}>
                {filename}
              </span>
              <span className="upload-id">{item.upload_id}</span>
              {item.error_message && (
                <div className="kb-error upload-row-error" role="alert">
                  <strong>{failed ? "处理失败" : "提示"}</strong>
                  <span className="kb-error-detail">{item.error_message}</span>
                </div>
              )}
            </div>
            <div className="upload-cell" role="cell">
              <span
                className={`upload-status-badge ${uploadStatusTone(item.status)}`}
                data-status={item.status}
              >
                {uploadStatusLabel(item.status)}
              </span>
              {!isBusyStatus(item.status) && item.progress != null && item.progress > 0 && (
                <span className="upload-progress-text">{item.progress}%</span>
              )}
              {isBusyStatus(item.status) && (
                <div
                  className="upload-row-progress"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={item.progress ?? 0}
                >
                  <div
                    className="upload-progress-bar"
                    style={{ width: `${item.progress ?? 0}%` }}
                  />
                </div>
              )}
            </div>
            <div className="upload-cell" role="cell">
              <span className="upload-format">{item.doc_format.toUpperCase()}</span>
              <span className="upload-size">{formatFileSize(item.file_size)}</span>
            </div>
            <div className="upload-cell" role="cell">
              <span>{item.chunk_count} 块</span>
              <span className="upload-engine">{engineLabel(item.parse_engine || "native")}</span>
            </div>
            <div className="upload-cell upload-actions" role="cell">
              <button
                type="button"
                className="source-action-btn"
                disabled={!canPreview}
                onClick={() => onPreview(item.upload_id)}
              >
                预览
              </button>
              {failed && (
                <button
                  type="button"
                  className="source-action-btn primary"
                  disabled={busy}
                  onClick={() =>
                    item.status === "failed_parse"
                      ? onReparse(item.upload_id)
                      : onReindex(item.upload_id)
                  }
                >
                  重试
                </button>
              )}
              {(item.status === "parsed" || item.status === "indexed") && (
                <button
                  type="button"
                  className="source-action-btn"
                  disabled={busy}
                  onClick={() => onReindex(item.upload_id)}
                >
                  重建索引
                </button>
              )}
              <button
                type="button"
                className="source-action-btn danger"
                disabled={busy}
                onClick={() => {
                  if (
                    window.confirm(`确定删除「${filename}」？向量与解析文件都会删除。`)
                  ) {
                    onDelete(item.upload_id);
                  }
                }}
              >
                删除
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

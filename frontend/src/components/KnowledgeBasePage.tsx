import { useState } from "react";
import { useUploads } from "../hooks/useUploads";
import type { UploadCreateResponse, UploadOptions } from "../types/upload";
import { engineLabel } from "../utils/upload";
import UploadDropzone from "./UploadDropzone";
import UploadList from "./UploadList";
import UploadPreviewDialog from "./UploadPreviewDialog";

type Tab = "upload" | "documents" | "engines";

interface Props {
  /** 测试可缩短轮询间隔；生产默认 1 秒。 */
  pollIntervalMs?: number;
}

const TAB_LABELS: Array<{ id: Tab; label: string }> = [
  { id: "upload", label: "上传" },
  { id: "documents", label: "文档" },
  { id: "engines", label: "引擎" },
];

export default function KnowledgeBasePage({ pollIntervalMs }: Props) {
  const [tab, setTab] = useState<Tab>("documents");
  const [previewUploadId, setPreviewUploadId] = useState<string | null>(null);
  const uploads = useUploads({ pollIntervalMs });

  const handleUpload = async (
    files: File[],
    options: UploadOptions,
  ): Promise<UploadCreateResponse | null> => {
    const result = await uploads.upload(files, options);
    if (result) setTab("documents");
    return result;
  };

  const handleDelete = async (uploadId: string) => {
    const ok = await uploads.remove(uploadId);
    if (ok && previewUploadId === uploadId) setPreviewUploadId(null);
  };

  return (
    <div className="knowledge-shell">
      <header className="knowledge-header glass">
        <div>
          <h1>知识库</h1>
          <p>上传文档、查看解析状态，并管理向量索引。</p>
        </div>
        <div className="knowledge-header-actions">
          <span className="kb-count-pill">共 {uploads.total} 篇</span>
          <a className="ghost-button" href="#/chat">
            返回对话
          </a>
        </div>
      </header>

      <nav className="knowledge-tabs glass" role="tablist" aria-label="知识库页签">
        {TAB_LABELS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            className={`knowledge-tab ${tab === item.id ? "active" : ""}`}
            onClick={() => setTab(item.id)}
          >
            {item.label}
            {item.id === "documents" && uploads.total > 0 ? `（${uploads.total}）` : ""}
          </button>
        ))}
      </nav>

      {uploads.error && (
        <div className="banner error glass kb-error-banner" role="alert">
          <div className="kb-error-head">
            <strong>知识库操作失败</strong>
            <div>
              <button type="button" className="source-action-btn" onClick={() => void uploads.refresh()}>
                重试
              </button>
              <button type="button" className="source-action-btn" onClick={uploads.clearError}>
                关闭
              </button>
            </div>
          </div>
          <span className="kb-error-detail">{uploads.error}</span>
        </div>
      )}

      <main className="knowledge-main">
        {tab === "upload" && (
          <section role="tabpanel" aria-label="上传">
            <UploadDropzone
              engines={uploads.engines}
              uploading={uploads.uploading}
              progress={uploads.uploadProgress}
              disabled={uploads.uploading}
              onUpload={handleUpload}
            />
          </section>
        )}

        {tab === "documents" && (
          <section role="tabpanel" aria-label="文档" className="knowledge-documents">
            <div className="knowledge-section-head">
              <div>
                <h2>上传文档</h2>
                <p>列表每秒自动刷新；离开本页后停止轮询。</p>
              </div>
              <button
                type="button"
                className="ghost-button"
                onClick={() => void uploads.refresh()}
                disabled={uploads.loading}
              >
                {uploads.loading ? "刷新中…" : "刷新"}
              </button>
            </div>
            <UploadList
              items={uploads.items}
              loading={uploads.loading}
              actionBusy={uploads.actionBusy}
              onPreview={setPreviewUploadId}
              onReparse={(id) => void uploads.reparse(id)}
              onReindex={(id) => void uploads.reindex(id)}
              onDelete={(id) => void handleDelete(id)}
            />
          </section>
        )}

        {tab === "engines" && (
          <section role="tabpanel" aria-label="引擎" className="knowledge-engines">
            <div className="knowledge-section-head">
              <div>
                <h2>解析引擎</h2>
                <p>引擎健康状态缓存 30 秒；不可用的引擎不会阻断其他功能。</p>
              </div>
              <button
                type="button"
                className="ghost-button"
                onClick={() => void uploads.loadEngines()}
              >
                重新检测
              </button>
            </div>
            {uploads.engineError && (
              <div className="banner error glass" role="alert">
                引擎状态获取失败：{uploads.engineError}
              </div>
            )}
            <div className="engine-grid">
              {uploads.engines.map((engine) => (
                <article className="engine-card glass" key={engine.name}>
                  <div className="engine-card-head">
                    <h3>{engineLabel(engine.name)}</h3>
                    <span
                      className={`upload-status-badge ${engine.available ? "ok" : "error"}`}
                    >
                      {engine.available ? "可用" : "不可用"}
                    </span>
                  </div>
                  <div className="engine-card-detail">
                    {engine.health?.detail || engine.health?.status || "等待健康检查"}
                  </div>
                  <div className="engine-formats">
                    支持格式：
                    {engine.formats.length > 0 ? engine.formats.join(" / ") : "暂无"}
                  </div>
                </article>
              ))}
              {uploads.engines.length === 0 && !uploads.engineError && (
                <div className="upload-list-empty glass">正在检测引擎…</div>
              )}
            </div>
          </section>
        )}
      </main>

      <UploadPreviewDialog
        uploadId={previewUploadId}
        onClose={() => setPreviewUploadId(null)}
      />
    </div>
  );
}

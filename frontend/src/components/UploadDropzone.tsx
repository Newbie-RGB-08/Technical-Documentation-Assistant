import { useCallback, useRef, useState } from "react";
import type {
  EngineInfo,
  UploadCreateResponse,
  UploadOptions,
} from "../types/upload";
import {
  ACCEPTED_EXTENSIONS,
  MAX_UPLOAD_FILES,
  MAX_UPLOAD_MB,
  UPLOAD_ACCEPT,
  engineLabel,
  fileExtension,
  formatFileSize,
} from "../utils/upload";

interface Props {
  engines: EngineInfo[];
  uploading: boolean;
  progress: number;
  disabled?: boolean;
  maxFiles?: number;
  maxMB?: number;
  onUpload: (files: File[], options: UploadOptions) => Promise<UploadCreateResponse | null>;
}

function validateFile(file: File, maxMB: number): string | null {
  const ext = fileExtension(file.name);
  if (!ext || !ACCEPTED_EXTENSIONS.includes(ext)) {
    return `${file.name}：不支持 ${ext || "无扩展名"}；支持 ${ACCEPTED_EXTENSIONS.join(" ")}`;
  }
  if (file.size === 0) return `${file.name}：文件为空`;
  if (file.size > maxMB * 1024 * 1024) return `${file.name}：文件超过 ${maxMB} MB`;
  return null;
}

export default function UploadDropzone({
  engines,
  uploading,
  progress,
  disabled = false,
  maxFiles = MAX_UPLOAD_FILES,
  maxMB = MAX_UPLOAD_MB,
  onUpload,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [engine, setEngine] = useState("auto");
  const [autoIndex, setAutoIndex] = useState(true);
  const [chunkSize, setChunkSize] = useState(512);
  const [chunkOverlap, setChunkOverlap] = useState(64);
  const [tags, setTags] = useState("");
  const [force, setForce] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const addFiles = useCallback(
    (incoming: File[]) => {
      const errors: string[] = [];
      const next = [...files];
      for (const file of incoming) {
        if (next.length >= maxFiles) {
          errors.push(`一次最多上传 ${maxFiles} 个文件`);
          break;
        }
        if (next.some((item) => item.name === file.name && item.size === file.size)) {
          continue;
        }
        const problem = validateFile(file, maxMB);
        if (problem) {
          errors.push(problem);
          continue;
        }
        next.push(file);
      }
      setFiles(next);
      setError(errors.length > 0 ? errors.join("\n") : null);
    },
    [files, maxFiles, maxMB],
  );

  const handleInput = (event: React.ChangeEvent<HTMLInputElement>) => {
    addFiles(Array.from(event.target.files ?? []));
    event.target.value = "";
  };

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (disabled || uploading) return;
    addFiles(Array.from(event.dataTransfer.files ?? []));
  };

  const removeFile = (index: number) => {
    setFiles((previous) => previous.filter((_, i) => i !== index));
  };

  const submit = async () => {
    if (files.length === 0 || uploading || submitting || disabled) return;
    if (chunkSize < 64 || chunkSize > 4096) {
      setError("chunk_size 必须在 64~4096 之间");
      return;
    }
    if (chunkOverlap < 0 || chunkOverlap >= chunkSize) {
      setError("chunk_overlap 必须小于 chunk_size");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await onUpload(files, {
        engine,
        auto_index: autoIndex,
        chunk_size: chunkSize,
        chunk_overlap: chunkOverlap,
        tags: tags.trim(),
        force,
      });
      if (result) {
        setFiles([]);
        setTags("");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const busy = uploading || submitting;
  const engineOptions = [
    { name: "auto", available: true, formats: [] as string[] },
    ...engines.filter((item) => item.name !== "auto"),
  ];

  return (
    <section className="upload-dropzone-panel glass" aria-label="上传文档">
      <div
        className={`upload-dropzone ${dragging ? "dragging" : ""} ${disabled ? "disabled" : ""}`}
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-label="拖拽或选择文件"
        aria-disabled={disabled || busy}
        onClick={() => {
          if (!disabled && !busy) inputRef.current?.click();
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (!disabled && !busy) inputRef.current?.click();
          }
        }}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled && !busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
      >
        <span className="upload-dropzone-icon" aria-hidden="true">
          ⇪
        </span>
        <span className="upload-dropzone-title">把文档拖到这里，或点击选择</span>
        <span className="upload-dropzone-hint">
          支持 {ACCEPTED_EXTENSIONS.join(" / ")}；单文件 ≤ {maxMB} MB，单批最多 {maxFiles} 个
        </span>
        <input
          ref={inputRef}
          className="visually-hidden"
          type="file"
          multiple
          accept={UPLOAD_ACCEPT}
          aria-label="选择文件"
          onChange={handleInput}
          disabled={disabled || busy}
        />
      </div>

      {files.length > 0 && (
        <ul className="upload-selected-list" aria-label="待上传文件">
          {files.map((file, index) => (
            <li key={`${file.name}-${file.size}-${file.lastModified}`} className="upload-selected-item">
              <span className="upload-selected-name">{file.name}</span>
              <span className="upload-selected-size">{formatFileSize(file.size)}</span>
              <button
                type="button"
                className="source-action-btn"
                onClick={() => removeFile(index)}
                disabled={busy}
                aria-label={`移除 ${file.name}`}
              >
                移除
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="upload-options">
        <label className="kb-field">
          <span>解析引擎</span>
          <select
            value={engine}
            onChange={(event) => setEngine(event.target.value)}
            disabled={busy}
            aria-label="解析引擎"
          >
            {engineOptions.map((item) => (
              <option key={item.name} value={item.name} disabled={!item.available}>
                {engineLabel(item.name)}
                {item.available ? "" : "（不可用）"}
              </option>
            ))}
          </select>
        </label>
        <label className="kb-checkbox">
          <input
            type="checkbox"
            checked={autoIndex}
            onChange={(event) => setAutoIndex(event.target.checked)}
            disabled={busy}
          />
          解析后自动入库
        </label>
        <label className="kb-checkbox">
          <input
            type="checkbox"
            checked={force}
            onChange={(event) => setForce(event.target.checked)}
            disabled={busy}
          />
          允许重复内容
        </label>
        <label className="kb-field kb-field-wide">
          <span>标签（逗号分隔，可选）</span>
          <input
            type="text"
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            placeholder="例如：Python, FastAPI"
            disabled={busy}
          />
        </label>
        <details className="upload-advanced">
          <summary>高级切片设置</summary>
          <div className="upload-advanced-grid">
            <label className="kb-field">
              <span>chunk_size</span>
              <input
                type="number"
                min={64}
                max={4096}
                value={chunkSize}
                onChange={(event) => setChunkSize(Number(event.target.value))}
                disabled={busy}
              />
            </label>
            <label className="kb-field">
              <span>chunk_overlap</span>
              <input
                type="number"
                min={0}
                max={4095}
                value={chunkOverlap}
                onChange={(event) => setChunkOverlap(Number(event.target.value))}
                disabled={busy}
              />
            </label>
          </div>
        </details>
      </div>

      {error && (
        <div className="kb-error" role="alert">
          <strong>上传前校验失败</strong>
          <span className="kb-error-detail">{error}</span>
        </div>
      )}

      {uploading && (
        <div className="upload-progress-row">
          <div
            className="upload-progress"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
          >
            <div className="upload-progress-bar" style={{ width: `${progress}%` }} />
          </div>
          <span>{progress}%</span>
        </div>
      )}

      <div className="upload-submit-row">
        <span className="upload-submit-hint">已选择 {files.length} 个文件</span>
        <button
          type="button"
          className="primary-button"
          onClick={() => void submit()}
          disabled={files.length === 0 || busy || disabled}
        >
          {uploading || submitting ? "上传中…" : "开始上传"}
        </button>
      </div>
    </section>
  );
}

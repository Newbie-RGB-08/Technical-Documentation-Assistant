import { useEffect, useState } from "react";
import type { ConfirmPayload } from "../types/chat";

interface Props {
  payload: ConfirmPayload;
  onConfirm: (edited?: string) => void;
  onRecord: () => void;
  onClose: () => void;
}

export default function ConfirmDialog({ payload, onConfirm, onRecord, onClose }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(payload.rewritten_question);

  useEffect(() => {
    setEditing(false);
    setDraft(payload.rewritten_question);
  }, [payload]);

  return (
    <div className="confirm-backdrop" role="presentation">
      <section className="confirm-dialog glass" role="dialog" aria-modal="true" aria-label="问题确认">
        <div className="panel-title">需要先确认你的问题</div>
        <div className="confirm-field">
          <div className="confirm-label">原始问题</div>
          <div className="confirm-original">{payload.original_question}</div>
        </div>
        <div className="confirm-field">
          <div className="confirm-label">系统理解</div>
          {editing ? (
            <textarea value={draft} rows={4} onChange={(event) => setDraft(event.target.value)} />
          ) : (
            <div className="confirm-rewritten">{payload.rewritten_question}</div>
          )}
        </div>
        <div className="confirm-actions">
          <button
            type="button"
            className="primary-button"
            onClick={() => onConfirm(editing ? draft : payload.rewritten_question)}
          >
            确认
          </button>
          <button type="button" className="ghost-button" onClick={() => setEditing((v) => !v)}>
            修改
          </button>
          <button type="button" className="ghost-button" onClick={onRecord}>
            记录此问题
          </button>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭确认框">
            ×
          </button>
        </div>
      </section>
    </div>
  );
}

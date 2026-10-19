import { useEffect, useRef, useState } from "react";

interface Props {
  streaming: boolean;
  onSend: (value: string) => void;
  onStop: () => void;
}

export default function ChatInput({ streaming, onSend, onStop }: Props) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [value]);

  const submit = () => {
    const text = value.trim();
    if (!text || streaming) return;
    onSend(text);
    setValue("");
  };

  return (
    <div className="chat-input glass">
      <textarea
        ref={textareaRef}
        value={value}
        rows={1}
        placeholder="向你的技术知识库提问，Enter 发送，Shift+Enter 换行"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
      />
      {streaming ? (
        <button type="button" className="primary-button danger" onClick={onStop}>
          停止
        </button>
      ) : (
        <button type="button" className="primary-button" onClick={submit} disabled={!value.trim()}>
          发送
        </button>
      )}
    </div>
  );
}

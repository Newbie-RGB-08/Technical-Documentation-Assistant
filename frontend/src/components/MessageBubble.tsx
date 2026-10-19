import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/common";
import "highlight.js/styles/github-dark.css";
import { marked } from "marked";
import { useEffect, useMemo, useRef } from "react";
import type { ChatMessage } from "../types/chat";

function renderMarkdown(content: string): string {
  const raw = marked.parse(content, { async: false }) as string;
  const clean = DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
  return clean.replace(
    /\[(\d{1,2})\]/g,
    '<button type="button" class="citation-link" data-citation="$1" aria-label="来源 $1">[$1]</button>',
  );
}

interface Props {
  message: ChatMessage;
  onCitationClick?: (index: number) => void;
}

export default function MessageBubble({ message, onCitationClick }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const html = useMemo(() => renderMarkdown(message.content || ""), [message.content]);

  useEffect(() => {
    rootRef.current
      ?.querySelectorAll("pre code")
      .forEach((el) => hljs.highlightElement(el as HTMLElement));
  }, [html]);

  const handleClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const target = (event.target as HTMLElement).closest("[data-citation]");
    if (!target) return;
    const index = Number(target.getAttribute("data-citation"));
    if (Number.isFinite(index)) onCitationClick?.(index);
  };

  return (
    <article className={`message-row ${message.role}`}>
      <div className={`message-bubble glass ${message.role}`}>
        <div className="message-role">{message.role === "user" ? "你" : "助手"}</div>
        <div
          ref={rootRef}
          className="markdown-body"
          onClick={handleClick}
          dangerouslySetInnerHTML={{ __html: html }}
        />
        {message.pending && <span className="typing-cursor" aria-label="正在生成" />}
        {message.error && <div className="message-error">{message.error}</div>}
      </div>
    </article>
  );
}

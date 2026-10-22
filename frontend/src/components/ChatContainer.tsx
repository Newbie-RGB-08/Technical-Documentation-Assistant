import { useEffect, useRef, useState } from "react";
import { useChatStream } from "../hooks/useChatStream";
import { useProviderStatus } from "../hooks/useProviderStatus";
import ChatInput from "./ChatInput";
import CitationPanel from "./CitationPanel";
import ConfirmDialog from "./ConfirmDialog";
import MessageBubble from "./MessageBubble";
import ModelSelector from "./ModelSelector";

/** V1.4：检索阶段文案 */
const STAGE_LABELS: Record<string, string> = {
  scoring: "评分中",
  local_retrieval: "本地检索",
  web_searching: "联网搜索",
  web_fetching: "抓取网页",
  generating: "生成中",
  verifying: "校验中",
  repairing: "修复中",
  writing_back: "写回中",
  general_streaming: "通用回答",
};

export default function ChatContainer() {
  const [modelId, setModelId] = useState<string | null>(null);
  const chat = useChatStream(modelId);
  const provider = useProviderStatus(modelId, setModelId);
  const listRef = useRef<HTMLDivElement>(null);
  const lastAssistant = [...chat.messages].reverse().find((m) => m.role === "assistant");

  // V1.5.x：meta.selected.auto（自动绑定）→ 每轮请求后刷新一次模型可用性
  const autoSelected = chat.meta?.selected?.auto === true;
  const refreshedForRef = useRef<string | null>(null);
  useEffect(() => {
    const requestId = chat.meta?.request_id ?? null;
    if (autoSelected && requestId && refreshedForRef.current !== requestId) {
      refreshedForRef.current = requestId;
      void provider.refresh();
    }
  }, [autoSelected, chat.meta?.request_id, provider.refresh]);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat.messages, chat.streaming]);

  // V1.4 优化：宽屏下拿到来源后自动展开来源面板
  useEffect(() => {
    if (chat.sources.length === 0) return;
    if (typeof window.matchMedia !== "function") return;
    if (window.matchMedia("(min-width: 1200px)").matches) {
      chat.setPanelOpen(true);
    }
  }, [chat.sources.length, chat.setPanelOpen]);

  return (
    <div className="app-shell">
      <header className="app-header glass">
        <div>
          <h1>PTDRA 对话工作台</h1>
          <p>个人技术文档研究助手</p>
        </div>
        <div className="header-actions">
          {chat.stage && STAGE_LABELS[chat.stage] && (
            <span className="stage-pill">{STAGE_LABELS[chat.stage]}</span>
          )}
          {chat.meta && (
            <span className="meta-pill">
              {chat.meta.provider} / {chat.meta.model}
            </span>
          )}
          <ModelSelector value={modelId} onChange={setModelId} models={provider.models} loaded={provider.loaded} />
          <button
            type="button"
            className="ghost-button"
            disabled={chat.sources.length === 0}
            onClick={() => chat.setPanelOpen(true)}
          >
            来源（{chat.sources.length}）
          </button>
        </div>
      </header>

      {provider.banner && (
        <div className={`banner ${provider.banner.kind} glass`} role="status">
          <span>{provider.banner.text}</span>
          {provider.banner.kind === "error" && provider.banner.detail && (
            <span className="detail">{provider.banner.detail}</span>
          )}
        </div>
      )}
      {autoSelected && provider.banner?.kind !== "error" && (
        <div className="banner notice glass" role="status">
          <span>配置中的模型未安装，系统已自动选用本地模型回答；可在右上角下拉框切换。</span>
        </div>
      )}
      {chat.error && <div className="banner error glass">{chat.error}</div>}
      {chat.notice && <div className="banner notice glass">{chat.notice}</div>}

      <main className="chat-main">
        <div ref={listRef} className="message-list">
          {chat.messages.length === 0 ? (
            <section className="empty-state glass">
              <h2>从你的文档开始</h2>
              <p>试试：“ChromaDB PersistentClient 怎么配置？”</p>
            </section>
          ) : (
            chat.messages.map((message) => (
              <MessageBubble
                key={message.id}
                message={message}
                onCitationClick={chat.openCitation}
                onRetry={message.role === "assistant" ? chat.retry : undefined}
                onOpenSources={() => chat.setPanelOpen(true)}
              />
            ))
          )}
        </div>
      </main>

      <footer className="app-footer">
        <ChatInput streaming={chat.streaming} onSend={chat.send} onStop={chat.stop} />
      </footer>

      <CitationPanel
        sources={chat.sources}
        activeIndex={chat.activeSource}
        open={chat.panelOpen}
        onClose={() => chat.setPanelOpen(false)}
        onSelect={chat.setActiveSource}
        answerStatus={lastAssistant?.answer_status ?? null}
      />

      {chat.confirmPayload && (
        <ConfirmDialog
          payload={chat.confirmPayload}
          onConfirm={chat.submitConfirm}
          onRecord={chat.recordConfirm}
          onClose={chat.closeConfirm}
        />
      )}
    </div>
  );
}

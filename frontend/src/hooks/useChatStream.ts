import { useCallback, useMemo, useReducer, useRef, useState } from "react";
import { postFeedback, streamChat } from "../api/client";
import type { ParsedSSE } from "../api/sse";
import type {
  AnswerStatus,
  ChatMessage,
  ConfirmPayload,
  DonePayload,
  MetaPayload,
  SourceItem,
  StatusPayload,
} from "../types/chat";
import { confirmReducer, initialConfirmState } from "./confirmMachine";

function uid(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function useChatStream(modelId: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sources, setSources] = useState<SourceItem[]>([]);
  const [activeSource, setActiveSource] = useState<number | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [meta, setMeta] = useState<MetaPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [confirmPayload, setConfirmPayload] = useState<ConfirmPayload | null>(null);
  const [confirmState, dispatchConfirm] = useReducer(confirmReducer, initialConfirmState);

  const sessionIdRef = useRef<string>(crypto.randomUUID());
  const abortRef = useRef<AbortController | null>(null);
  const lastQuestionRef = useRef<string>("");

  const patchMessage = useCallback((id: string, patch: Partial<ChatMessage>) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  }, []);

  const send = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || streaming) return;
      setError(null);
      setNotice(null);
      setStage(null);
      setConfirmPayload(null);
      lastQuestionRef.current = text;

      const assistantId = uid("a");
      const userMsg: ChatMessage = { id: uid("u"), role: "user", content: text };
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        pending: true,
        answer_status: "pending",
        interrupted: false,
      };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setStreaming(true);
      abortRef.current = new AbortController();
      let answer = "";

      const handleEvent = ({ event, data }: ParsedSSE) => {
        switch (event) {
          case "meta":
            setMeta(data as MetaPayload);
            break;
          case "status": {
            const payload = data as StatusPayload;
            setStage(payload.stage ?? null);
            break;
          }
          case "sources": {
            const items = data as SourceItem[];
            setSources(items);
            patchMessage(assistantId, { sources: items });
            break;
          }
          case "content":
            answer += String(data.text ?? "");
            patchMessage(assistantId, { content: answer, pending: true });
            break;
          case "confirm": {
            const payload = data as ConfirmPayload;
            setConfirmPayload(payload);
            dispatchConfirm({
              type: "RECEIVE_CONFIRM",
              original: payload.original_question,
              rewritten: payload.rewritten_question,
            });
            setMessages((prev) => prev.filter((m) => m.id !== assistantId));
            setStreaming(false);
            break;
          }
          case "error":
            setError(String(data.message ?? "未知错误"));
            patchMessage(assistantId, {
              pending: false,
              error: String(data.message ?? "未知错误"),
              answer_status: "error",
            });
            break;
          case "done": {
            const done = data as DonePayload;
            if (done.session_id) sessionIdRef.current = done.session_id;
            patchMessage(assistantId, {
              pending: false,
              answer_status: (done.answer_status as AnswerStatus) ?? "verified",
              interrupted: Boolean(done.interrupted),
              request_id: done.request_id,
            });
            setStage(null);
            setStreaming(false);
            break;
          }
          default:
            break;
        }
      };

      try {
        await streamChat({
          question: text,
          sessionId: sessionIdRef.current,
          modelId,
          signal: abortRef.current.signal,
          onEvent: handleEvent,
        });
      } catch (exc) {
        if ((exc as Error).name !== "AbortError") {
          const message = (exc as Error).message || "对话失败";
          setError(message);
          patchMessage(assistantId, {
            pending: false,
            error: message,
            answer_status: "error",
          });
        } else {
          patchMessage(assistantId, { pending: false, interrupted: true, answer_status: "interrupted" });
        }
      } finally {
        setStage(null);
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [modelId, patchMessage, streaming],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setStage(null);
    setStreaming(false);
    setMessages((prev) =>
      prev.map((m) =>
        m.pending
          ? {
              ...m,
              pending: false,
              interrupted: true,
              answer_status: "interrupted",
              content: m.content || "（已停止）",
            }
          : m,
      ),
    );
  }, []);

  const retry = useCallback(() => {
    const question = lastQuestionRef.current;
    if (!question || streaming) return;
    void send(question);
  }, [send, streaming]);

  const openCitation = useCallback((index: number) => {
    setActiveSource(index);
    setPanelOpen(true);
  }, []);

  const submitConfirm = useCallback(
    (edited?: string) => {
      dispatchConfirm({ type: "CONFIRM" });
      const next = (edited ?? confirmState.draft ?? confirmState.rewritten).trim();
      setConfirmPayload(null);
      if (next) void send(next);
    },
    [confirmState.draft, confirmState.rewritten, send],
  );

  const recordConfirm = useCallback(() => {
    if (confirmPayload) {
      void postFeedback({
        session_id: sessionIdRef.current,
        question: confirmPayload.original_question,
      }).catch(() => undefined);
    }
    dispatchConfirm({ type: "RECORD" });
    setConfirmPayload(null);
    setNotice("问题已记录，可在后端 feedback 表回顾。");
  }, [confirmPayload]);

  return useMemo(
    () => ({
      messages,
      sources,
      activeSource,
      panelOpen,
      meta,
      error,
      notice,
      streaming,
      stage,
      confirmPayload,
      sessionId: sessionIdRef.current,
      send,
      stop,
      retry,
      openCitation,
      setPanelOpen,
      setActiveSource,
      submitConfirm,
      recordConfirm,
      closeConfirm: () => {
        dispatchConfirm({ type: "RESET" });
        setConfirmPayload(null);
      },
    }),
    [
      messages,
      sources,
      activeSource,
      panelOpen,
      meta,
      error,
      notice,
      streaming,
      stage,
      confirmPayload,
      send,
      stop,
      retry,
      openCitation,
      submitConfirm,
      recordConfirm,
    ],
  );
}

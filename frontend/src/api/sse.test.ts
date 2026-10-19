import { describe, expect, it, vi } from "vitest";
import { createSSEParser, parseSSEBlock } from "./sse";

describe("parseSSEBlock", () => {
  it("解析 event + 单行 data（JSON）", () => {
    const parsed = parseSSEBlock('event: meta\ndata: {"model":"qwen2.5:7b"}');
    expect(parsed).toEqual({ event: "meta", data: { model: "qwen2.5:7b" } });
  });

  it("多行 data 用换行拼接", () => {
    const parsed = parseSSEBlock("event: content\ndata: 第一行\ndata: 第二行");
    expect(parsed).toEqual({ event: "content", data: "第一行\n第二行" });
  });

  it("无 event 行时默认 message", () => {
    const parsed = parseSSEBlock('data: {"a":1}');
    expect(parsed?.event).toBe("message");
  });

  it("非 JSON data 原样返回字符串", () => {
    const parsed = parseSSEBlock("event: content\ndata: 普通文本");
    expect(parsed?.data).toBe("普通文本");
  });

  it("空块或无 data 返回 null", () => {
    expect(parseSSEBlock("")).toBeNull();
    expect(parseSSEBlock("   ")).toBeNull();
    expect(parseSSEBlock("event: done")).toBeNull();
  });
});

describe("createSSEParser 分帧", () => {
  it("完整块一次性到达", () => {
    const onEvent = vi.fn();
    const parser = createSSEParser(onEvent);
    parser.push('event: meta\ndata: {"a":1}\n\nevent: done\ndata: {"b":2}\n\n');
    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onEvent.mock.calls[0][0]).toEqual({ event: "meta", data: { a: 1 } });
    expect(onEvent.mock.calls[1][0]).toEqual({ event: "done", data: { b: 2 } });
  });

  it("块被拆成多个 chunk 到达（跨 chunk 分帧）", () => {
    const onEvent = vi.fn();
    const parser = createSSEParser(onEvent);
    parser.push("event: con");
    parser.push('tent\ndata: {"text":"你');
    expect(onEvent).not.toHaveBeenCalled();
    parser.push('好"}\n\n');
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent.mock.calls[0][0]).toEqual({ event: "content", data: { text: "你好" } });
  });

  it("分隔符本身被拆开（\\n 与 \\n 分两次到达）", () => {
    const onEvent = vi.fn();
    const parser = createSSEParser(onEvent);
    parser.push('data: {"x":1}\n');
    expect(onEvent).not.toHaveBeenCalled();
    parser.push("\n");
    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it("兼容 \\r\\n 换行", () => {
    const onEvent = vi.fn();
    const parser = createSSEParser(onEvent);
    parser.push('event: meta\r\ndata: {"a":1}\r\n\r\n');
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent.mock.calls[0][0].event).toBe("meta");
  });

  it("flush 处理流末尾无空行的残留块", () => {
    const onEvent = vi.fn();
    const parser = createSSEParser(onEvent);
    parser.push('event: done\ndata: {"session_id":"s1"}');
    expect(onEvent).not.toHaveBeenCalled();
    parser.flush();
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent.mock.calls[0][0]).toEqual({ event: "done", data: { session_id: "s1" } });
  });

  it("块间空行/心跳注释不产生事件", () => {
    const onEvent = vi.fn();
    const parser = createSSEParser(onEvent);
    parser.push("\n\n: heartbeat\n\nevent: meta\ndata: {}\n\n");
    expect(onEvent).toHaveBeenCalledTimes(1);
  });
});

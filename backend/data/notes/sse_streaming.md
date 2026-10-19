---
title: SSE 流式接口设计
tags: [sse, fastapi, 流式输出]
created_at: 2026-10-10
---

# SSE 流式接口设计

## 协议格式

Server-Sent Events 每个事件块以空行分隔：

```
event: content
data: {"text": "你好"}

event: done
data: {"session_id": "abc"}

```

- `event:` 指定事件名，`data:` 可以有多行（客户端用 \n 拼接）。
- 注释行以 `:` 开头，可用作心跳。

## FastAPI 实现

```python
from fastapi.responses import StreamingResponse

async def event_stream():
    yield "event: meta\ndata: {}\n\n"
    yield "event: content\ndata: {\"text\": \"你好\"}\n\n"

@router.post("/chat/stream")
async def chat():
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

## 前端解析要点

1. fetch + ReadableStream 读取，按 `\n\n` 分帧；注意帧可能跨 chunk 到达，要维护 buffer。
2. AbortController 中断后服务端要检测断连并停止生成。
3. 错误要作为独立 `error` 事件下发，而不是直接断开连接，前端才能给出友好提示。

---
title: Ollama 本地模型运维
tags: [ollama, llm, 本地部署]
created_at: 2026-10-02
---

# Ollama 本地模型运维

## 常用命令

```bash
ollama pull qwen2.5:7b      # 拉取模型
ollama list                 # 查看已安装模型
ollama serve                # 启动服务（默认 11434 端口）
ollama ps                   # 查看运行中的模型
```

## API 调用

```bash
curl http://localhost:11434/api/generate -d '{
  "model": "qwen2.5:7b",
  "prompt": "你好",
  "stream": true
}'
```

## 性能经验

- 16GB 内存 + 核显机器跑 7B Q4 量化模型约 8~12 token/s。
- `num_ctx` 默认 2048，长上下文需要在 Modelfile 或请求里显式调大。
- 模型首次加载较慢（数秒），之后常驻内存；`keep_alive` 控制卸载时间。

## 故障排查

1. `/api/tags` 不可达：先确认 `ollama serve` 已启动。
2. 拉取失败多为网络问题，可重试或配置镜像代理。
3. 生成中断常见于显存不足，换更小量化版本。

## 补充：keep_alive 参数

`keep_alive` 支持秒数或时长字符串（如 `10m`），负数表示常驻内存。


# PTDRA · 个人技术文档研究助手

> Personal Technical Documentation Research Assistant · V1（2026-10-19 里程碑）

专属于个人使用的技术文档研究助手。把本地 Markdown 笔记和爬取的技术文档接入进来，用 **混合检索 + RRF 融合 + Rerank** 回答你的技术问题，回答带 [来源N] 引用标注和可点击的来源面板。

---

## V1 已实现

后端 FastAPI + 前端 React + 本地 Ollama，三层架构顺序交付，10 条验收全部通过：

| 阶段 | 模块 | 关键实现 | 测试 |
| --- | --- | --- | --- |
| 0 | 环境与项目骨架 | venv / MySQL / Ollama / ChromaDB | 4 项冒烟 |
| 1 | 数据采集层 | 标题感知分块 + 笔记扫描 + 爬虫（降级 httpx） | 10 单测 |
| 2 | 知识处理层 | MySQL 元数据 + SQLite 会话 + ChromaDB 向量 + BM25 增量 | 15 集成 |
| 3 | Agent 执行层核心 | 五维提问评分 / 检索定级 / 分级检索 + 降级 / 置信度 / 三类兜底 | 28 单测 |
| 4 | LLM 统一网关 | Provider 抽象 / models.yaml 路由链 / `${VAR}` 环境变量 / 降级重试 | 12 mock 测试 |
| 5 | FastAPI 后端组装 | SSE 六事件协议 / chat 流式 / confirm 确认 / error 事件 | 9 httpx 测试 |
| 6 | React 前端 | SSE buffer 分帧 / confirm 状态机 / 引用联动 / Liquid Glass 风格 | 22 vitest |
| 7 | 端到端联调 | 10 条实机验收 + 4 个缺陷修复 | 全量回归 **82 backend + 22 frontend** |

**V1 未做（见 PRD 附录）**：多用户权限 / 持久化前端会话 / 长期运维指标 / Docker Compose 一键启动（依赖宿主机预装 MySQL 与 Ollama）。

## 核心技术

- 检索策略：**L3** 向量 + BM25 → RRF 融合（k=60, 权重 1.0/0.7）→ bge-reranker-v2-m3 精排 → **L2** 无 Rerank → **L1** 纯向量（阈值 0.5）→ **L0** 兜底
- 提问评分：五维度 20×5，<60 触发 LLM 改写 + confirm 弹窗；越狱/敏感问题提前拒答
- 兜底：三类分流 — `sensitive` 拒答 / `kb_related` 未找到 / `general` 声明"未基于本地知识库"后自答
- 增量入库：SHA-256 哈希变更检测，修改文件仅更新对应向量 ID（含 content_hash[:12] 后缀）

## 目录结构

```
PTDRA/
├── PRD.md                    # 需求文档（唯一需求依据）
├── DEVPLAN.md                # 开发计划 + V1 验收记录
├── backend/                  # Python 后端
│   ├── app/
│   │   ├── main.py           # FastAPI 入口
│   │   ├── state.py          # 启动装配
│   │   ├── config.py         # Pydantic Settings
│   │   ├── core/             # 纯逻辑层（检索/评分/兜底）
│   │   ├── providers/        # Ollama / OpenAI 兼容 / 本地 reranker
│   │   ├── services/         # 三库 + 网关 + RAG 编排
│   │   ├── routers/          # /api/chat/stream · /api/documents/* · /api/health
│   │   └── models/schemas.py
│   ├── tests/                # pytest（asyncio_mode=auto）
│   ├── config/models.yaml    # LLM 路由链
│   ├── data/                 # chroma_db / sessions.db / notes / tech_docs
│   ├── requirements.txt
│   └── scripts/              # smoke_env · init_mysql.sql · build_domain_terms · sse_client
└── frontend/                 # React + Vite + TS
    ├── src/
    │   ├── api/              # SSE 分帧 · fetch client
    │   ├── hooks/            # useChatStream · confirmMachine
    │   ├── components/       # ChatContainer · MessageBubble · CitationPanel · ConfirmDialog · ChatInput · ModelSelector
    │   └── styles.css        # Liquid Glass 主题
    ├── package.json          # dev / build / test / typecheck
    └── vite.config.ts        # /api → :8000 代理
```

## 快速启动

### 环境前置
- Python 3.11+（已验证 3.12.10）
- Node 18+
- 可用 MySQL 8.x（默认 `root / rgb123456!@# / rag_meta`，见 `backend/.env`）
- Ollama（localhost:11434，已拉取 `qwen2.5:7b` + `qwen2.5:3b`）
- 首次启动需要联网下载 `bge-small-zh-v1.5`（~90MB）和 `bge-reranker-v2-m3`（~2GB），已默认走 HF 镜像

### 后端
```bash
cd backend
pip install -r requirements.txt
# 如需改动 .env（MySQL/Ollama 地址等），复制 .env.example 为 .env

# 可选：跑一下冒烟
python scripts/smoke_env.py

pytest                  # 82 单测
uvicorn app.main:app --port 8000
```

### 前端
```bash
cd frontend
npm install --registry=https://registry.npmmirror.com \
             --cache <project-local-cache> --no-audit --no-fund

npm run test            # 22 vitest
npm run typecheck       # tsc --noEmit
npm run build           # 生产构建

npm run dev             # Vite dev，代理 /api → :8000
```

### 入库与对话
```bash
# 首次入库（笔记 + 爬取技术文档二选一或都做）
curl -X POST http://localhost:8000/api/documents/ingest \
     -H 'Content-Type: application/json' \
     -d '{"directory":"data/notes","source_type":"personal_note"}'

# 流式对话（SSE）
curl -N -X POST http://localhost:8000/api/chat/stream \
     -H 'Content-Type: application/json' \
     -d '{"question":"FastAPI 的 Depends 依赖注入怎么用？"}'
```

## 版本历史

| 版本 | 日期 | 说明 |
| --- | --- | --- |
| V1 | 2026-10-19 | 首个完整里程碑，8 个开发阶段全部落地，PRD 第 7 章 10 条验收通过，后端 82 + 前端 22 单测全绿 |

详细实现、验收记录与修复清单见 [DEVPLAN.md](./DEVPLAN.md)。需求约束以 [PRD.md](./PRD.md) 为准。

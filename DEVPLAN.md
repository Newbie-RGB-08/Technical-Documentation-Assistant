# PTDRA 开发计划（DEVPLAN）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 编写日期 | 2026-10-18 |
| 需求依据 | [PRD.md](./PRD.md)（唯一需求依据） |
| 工作流约定 | 顺序开发，每阶段结束跑测试，失败先修复再进入下一阶段 |

---

## 总览

以下阶段严格对应 PRD 第 5.1 节的 7 项 V1 交付物，按依赖关系排成 8 个阶段（0~7）：

| 阶段 | 内容 | 依赖 |
| --- | --- | --- |
| 0 | 环境与项目骨架 | 无 |
| 1 | 数据采集层（爬虫 + 本地扫描 + 分块） | 0 |
| 2 | 知识处理层（三库存储 + 增量入库） | 1 |
| 3 | Agent 执行层核心逻辑（评分/检索/兜底，可离线测） | 2 |
| 4 | LLM 统一网关 + 问题改写 | 0（可与 2/3 并行开发，串行验收） |
| 5 | FastAPI 后端组装（SSE 全流程） | 3 + 4 |
| 6 | React 前端（对话工作台） | 5 |
| 7 | 端到端联调 + 10 条验收 | 全部 |

约定：Python 3.11+、Node 18+、Windows PowerShell、所有文件 UTF-8；测试框架 pytest + pytest-asyncio + httpx。

---

## 阶段 0：环境与项目骨架

**任务**

1. 创建 PRD 3.5.1 定义的 `backend/` 目录骨架与 `frontend/` 占位、`config/`、`data/notes/`、`data/chroma_db/`
2. `backend/requirements.txt`：fastapi、uvicorn[standard]、chromadb、sentence-transformers、sqlalchemy、pymysql、aiomysql、langchain-text-splitters、jieba、rank-bm25、crawl4ai、openai、ollama、pydantic-settings、python-dotenv、pytest、pytest-asyncio、httpx
3. `app/config.py`（Pydantic Settings）+ `.env.example`（数据库连接、OLLAMA_HOST、各 API Key 占位）
4. `config/models.yaml`（按 PRD 3.4.3 写好四类任务路由链）
5. 基础设施就位：
   - Docker 启动 MySQL 8.0（PRD 第 6 章命令）
   - `ollama pull qwen2.5:7b`、`ollama pull qwen2.5:3b`
   - 首次 import 时自动下载 bge-small-zh-v1.5（约 90MB）

**测试（阶段出口）**

- `pytest` 空套件通过
- 冒烟脚本验证四项连通性：MySQL 可连、Ollama `/api/tags` 可达、ChromaDB 能建持久化 collection、bge 模型能对一句中文生成向量（512 维）

---

## 阶段 1：数据采集层

**任务**

1. `app/core/chunker.py`：`chunk_markdown()` —— 按 PRD 3.2.1 实现标题感知拆分 + RecursiveCharacterTextSplitter 递归切分，输出带 `heading_path / chunk_index / total_chunks` 的块列表
2. `app/services/note_scanner.py`：递归扫描 `.md`，提取 `created_at / updated_at / tags`（front-matter 或文件属性），组装文件级元数据
3. `app/services/crawler.py`：Crawl4AI 封装 —— URL 列表 + 深度配置、间隔 ≥2s、HTML→Markdown、去导航/侧边栏/页脚、保留代码块与表格、产出与本地笔记同构的入库对象（`source_type=tech_doc`）
4. 清洗结果的落盘约定：爬取的 Markdown 统一写入 `data/tech_docs/`，与笔记走同一条入库管道

**测试**

- 单测：给定一个含 `#/##/###` 和超长段落的样例 md，断言分块边界、heading_path 正确、无内容丢失、overlap 生效
- 单测：front-matter 解析、无标题文件的兜底行为
- 集成：爬取 1 个公开技术文档页面，断言产出 Markdown 含代码块且无导航噪声（允许跳过：需外网时改用本地 HTML fixture 测清洗函数）

---

## 阶段 2：知识处理层

**任务**

1. `app/services/vector_store.py`：实现 PRD 3.2.5 的 `VectorStore` 抽象（ChromaDB 版）—— 多集合（tech_docs / personal_notes）、批量 add（100/批）、query 带 metadata 过滤、按 id / 按 metadata 删除
2. `app/services/metadata_store.py`：MySQL 版 `MetadataStore` + 建表脚本（PRD 3.2.3 五张表，SQLAlchemy 模型或裸 SQL 均可，建库脚本放 `backend/scripts/init_mysql.sql`）
3. `app/services/session_store.py`：SQLite 会话/评分日志/反馈表
4. `app/services/ingest_pipeline.py`：增量入库 —— SHA-256 变更检测、删旧向量、重分块重向量化、`force_reingest` 支持、任务状态写 `ingest_tasks`
5. `app/core/bm25_index.py`：jieba 分词 + BM25Okapi 内存索引，支持随入库增量加载

**测试**

- 单测（内存 SQLite / 临时 Chroma 目录）：五张表建表与基本 CRUD
- 集成：放入 3 个样例 md → 入库 → 断言 Chroma chunk 数与 MySQL chunk 记录一致
- 集成（对应验收 #2/#3）：重复入库全部跳过；修改 1 个文件后入库，仅该文件 chroma_id 更换
- BM25：给定查询能返回排序合理的 top-k

---

## 阶段 3：Agent 执行层核心逻辑

**任务**

1. `app/core/question_scorer.py`：五维评分（PRD 3.3.1），领域词表从 MySQL 标题/标签自动构建，接 SQLite 近 7 天高频标签
2. `app/core/retrieval_scorer.py`：关键词权重评分 + 长度调节 + 指代惩罚 + `decide_level()`
3. `app/core/retriever.py`：分级检索 —— L1 纯向量（阈值 0.5）、L2 混合+RRF（k=60，权重 1.0/0.7）、L3 加 Rerank（bge-reranker-v2-m3）；四类降级触发与逐级回退
4. `app/core/confidence.py`：`compute_confidence()`（L3 rerank 分 / L2 RRF 归一化 / L1 相似度）
5. `app/core/fallback.py`：三类兜底分支（声明式自答 / 未找到 / 拒答）+ 禁止行为约束
6. 领域词表构建脚本 `scripts/build_domain_terms.py`

**测试（本阶段全部离线可测，是回归重点）**

- 评分单测：每个维度构造边界用例（指代词有无、越狱词、多问号……），断言分值与 PRD 表格一致
- `decide_level` 边界：39/40/59/60/79/80
- 检索单测（用阶段 2 的样例库）：明确 API 名问题走 L3、模糊问题命中降级路径；RRF 排序手算比对
- 置信度：三档阈值 0.3/0.6 边界用例
- 兜底：三类问题分流正确

---

## 阶段 4：LLM 统一网关

**任务**

1. `app/providers/base.py`：`LLMProvider / EmbeddingProvider / RerankProvider` 抽象（PRD 3.4.2）
2. `app/providers/ollama_provider.py`：原生 SDK，chat/stream/embed/is_available
3. `app/providers/openai_provider.py`：OpenAICompatibleProvider，`${VAR}` 环境变量解析
4. `app/services/llm_gateway.py`：`chat()/stream()/embed()/rerank()`；读 models.yaml 构建路由链；健康检查 + 逐级降级 + 超时重试（≤2 次）；全链失败抛友好错误
5. `app/core/query_rewriter.py`：3 轮历史改写（task_type=rewrite），无历史直通

**测试**

- 单测（mock Provider）：主链失败自动切下一链；全链失败抛 `AllProvidersFailedError`；流式接口逐 token 产出
- 冒烟（真实环境）：Ollama chat 一轮、embed 一句、stream 逐字输出、rerank 一对文档打分
- 改写：给定含"这个怎么配"的历史，断言输出为独立完整查询

---

## 阶段 5：FastAPI 后端组装

**任务**

1. `app/models/schemas.py`：IngestRequest/Response、ChatRequest、SSE 六类事件的 Pydantic 模型
2. `app/routers/documents.py`：`POST /api/documents/ingest`（BackgroundTasks 异步）、`GET /api/documents/status/{task_id}`
3. `app/routers/models.py`：`GET /api/models`（含 available 状态）
4. `app/routers/chat.py`：`POST /api/chat/stream` —— 按 PRD 3.5.2 串主流程：meta → 提问评分（<60 发 confirm 终止）→ 改写 → 检索评分定级 → 降级检索 → 置信度 →（兜底）→ sources → content 流式 → done；异常发 error 事件
5. `app/services/rag_service.py`：流程编排 + 会话历史读写 SQLite
6. `app/main.py`：CORS（放行 5173）、路由注册、启动时建表/建集合

**测试**

- httpx 异步测试 + mock 网关：断言 SSE 事件序列与载荷结构（meta → sources → content* → done；confirm 分支；error 分支）
- 真实冒烟：curl 发一条明确问题，肉眼验证流式与来源
- 入库接口：提交后台任务后轮询 status 直到 completed

---

## 阶段 6：React 前端

**任务**

1. Vite + React + TS 骨架；`api/client.ts`（fetch + SSE 解析）、`types/chat.ts`
2. `hooks/useChatStream.ts`：按 PRD 3.6.2 处理六类事件、AbortController 停止、confirm 状态
3. 组件：ChatContainer、MessageBubble（[N] 引用联动）、CitationPanel（侧滑）、SourceCard、ChatInput、ConfirmDialog（确认/修改/记录此问题）、ModelSelector
4. marked + DOMPurify + highlight.js 渲染链
5. macOS 风格主题（Liquid Glass 材质、毛玻璃卡片、空间层次）

**测试**

- `npm run build` 零错误；`tsc --noEmit` 通过
- vitest 关键单测：SSE buffer 分帧解析、confirm 流程状态机、引用点击联动
- 与后端联调：真机走一轮对话（流式、来源面板、停止按钮）

---

## 阶段 7：端到端联调与验收

**任务**

1. 准备验收数据：`data/notes/` 放 5+ 篇真实笔记；爬取 2~3 个文档页面入库
2. 逐条执行 PRD 第 7 章 10 条验收标准，记录结果
3. 修复问题 → 回归全部 pytest + vitest → 复验

**验收映射**（确保无遗漏）：

| 验收项 | 覆盖阶段 |
| --- | --- |
| #1~#3（入库/去重/增量） | 阶段 2 |
| #4~#7（L3 检索引用/确认弹窗/拒答/常识声明） | 阶段 5 |
| #8（Ollama 挂掉自动降级） | 阶段 4 |
| #9（SSE 流式/停止/error） | 阶段 5 + 6 |
| #10（网站爬取入库） | 阶段 1 + 2 |

阶段 7 全部实机复核一遍。

### 验收记录（2026-10-19 实机执行）

| # | 场景 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 本地笔记首次入库 | 通过 | 6 篇笔记 → 27 chunk，3 篇爬取文档 → 95 chunk，任务状态 completed |
| 2 | 重复入库 | 通过 | chunk_count=0，全部跳过 |
| 3 | 修改一个 md 再入库 | 通过 | 仅 ollama_ops.md 重入（5→6 chunk，hash 1fffdaa9→b23d0d1a），其余不变 |
| 4 | 含 API 名明确问题 | 通过 | level=3、confidence=0.998、答案带 [来源N]；浏览器验证来源面板点击联动正常 |
| 5 | 模糊问题确认 | 通过 | “这个怎么配？”触发 confirm 事件，含改写问题与 确认/修改/记录 选项 |
| 6 | 知识库不存在的问题 | 通过 | 明确回答“根据现有文档无法确定”，无编造引用 |
| 7 | 通用技术常识问题 | 通过 | 声明“以下回答未基于本地知识库，仅供参考。”后作答，不带 sources |
| 8 | 停掉 Ollama | 通过 | 未配置云端 Key，链路降级后下发友好 error：“所有模型均不可用（任务: generate）” |
| 9 | SSE 流式/停止/error | 通过 | 浏览器实测打字机流式、停止按钮中断恢复、error 事件友好提示 |
| 10 | 网站爬取入库 | 通过 | 3 个文档页爬取成功，代码块保留为 ``` 围栏（80/42/38 处），表格为管道格式 |

阶段 7 修复的问题（均有回归测试）：
- 入库后检索评分关键字权重未刷新 → 定级永远落空（`state.build_retrieval_scorer` + 入库后重建）
- 历史高频泛词（怎么/这个）抬升领域相关性 → 模糊问题绕过确认流程（停用词表 + 频次≥2）
- 中置信通用常识问题误用无关片段当来源 → 新增“声明后自答”分支（confidence<0.6 且 general）
- 爬虫 HTML→Markdown 代码块输出 `[code]` 标记 → 恢复为 ``` 围栏，保证分块器围栏保护生效

**最终回归**：后端 pytest 82/82 通过；前端 vitest 22/22 通过；`tsc --noEmit` 与 `vite build` 零错误。

---

## 阶段 8：本地检测、自动发现与自动绑定（V1.5.x，2026-10-22）

不绑定具体型号：自动探查本地已装模型并自动选用，前端下拉框可枚举到发现的模型。

| 模块 | 变更 |
| --- | --- |
| `providers/health.py`（新） | Reason 原因码 / LocalModelInfo / ProviderStatus / AutoBinding 域模型与 `list()` 解析（兼容 dict 与 pydantic 形态） |
| `providers/base.py` | `probe()` 具体默认实现委托旧 `is_available()`（测试 mock 开箱兼容） |
| `providers/ollama_provider.py` / `openai_provider.py` | 结构化 `probe(timeout)`：一次 `list()` 同时产出服务状态与已装模型清单；云端识别 401/403 → `auth_failed` |
| `services/llm_gateway.py` | `probe_all` + TTL 健康缓存 + 失效重探；`fit_binding` 自动绑定（家族匹配 → generate 取最大 / rewrite 取最小参数量 → 字典序 fallback）；候选链剔除已知不可用与“模型未装”项；`llm_timeout_seconds` 真正接入调用层 |
| `main.py` | lifespan 启动自检 + 三态启动日志（不阻断启动） |
| 协议 | `meta` 事件加可选 `selected:{provider,model,auto}`；`/api/models` 联合清单（`?refresh=`）；`/api/providers/status`；新错误码 `E_MODEL_UNAVAILABLE`（四象限引导，不点名型号） |
| 前端 | `useProviderStatus` 上提数据 + 横幅；`ModelSelector` 本地/云端分组 + 参数量后缀；`types/chat.ts`/`api/client.ts` 对应扩展 |
| 测试 | `tests/test_llm_gateway_health.py`（23 用例）、`tests/test_model_status_api.py`（12 用例）、前端 vitest 新增 9 用例 |

---

## 风险与预案

| 风险 | 影响 | 预案 |
| --- | --- | --- |
| Crawl4AI 在 Windows 需额外安装 Playwright 浏览器 | 阶段 1 | `crawl4ai-setup` 预置；失败则降级为 requests + readability 清洗，接口不变 |
| bge-reranker-v2-m3 体积大（约 2GB+）首次下载慢 | 阶段 3 | 阶段 0 预热下载；不可用时 L3 暂退化为 L2 行为，接口不变 |
| Docker Desktop 未安装 | 阶段 0 | 需先安装；或临时改用本机 MySQL，连接串走 .env |
| qwen2.5:7b 本地推理偏慢 | 阶段 5 | models.yaml 把 generate 首选换 qwen2.5:3b 或直连云端，改配置即可 |

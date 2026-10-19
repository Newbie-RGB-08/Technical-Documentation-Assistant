# 个人技术文档研究助手（PTDRA）产品需求文档

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 编写日期 | 2026-10-18 |
| 文档状态 | 关键决策已与需求方确认，作为后续开发唯一依据 |
| 需求来源 | 《个人技术文档研究助手.md》详细实现方案 + 需求方确认结论 |
| 服务对象 | 单人使用（需求方本人），无多用户/权限体系 |

---

## 1. 项目概述

### 1.1 背景与目标

做一个**个人专属的技术文档问答助手**：将技术资料（爬取的官方文档 + 本地 Markdown 笔记）统一入库，通过对话式界面提问，系统从知识库中检索并生成答案，**标注每条答案的来源（文件 + 章节）**。检索不到就明确说找不到，绝不编造。

产品核心性格：

- **问题模糊会反问**：如"这个怎么配？"，先改写理解并弹出确认框，不乱猜
- **检索分档降级**：问题越明确，检索链路越完整；失败自动降级重试
- **防幻觉硬约束**：低置信结果不硬答、不编造引用、不脑补扩写
- **模型可插拔**：本地 Ollama 优先，云端 API 自动兜底，配置文件随时切换
- **增量入库**：内容哈希变更检测，文档没改过就跳过

### 1.2 已确认的关键决策

以下决策已与需求方逐项确认，开发中不得擅自变更：

| 决策项 | 结论 | 对开发的影响 |
| --- | --- | --- |
| 模型运行环境 | 本地配置较好（16GB+ 内存 / 有独显） | 默认本地 Ollama qwen2.5:7b 生成、qwen2.5:3b 改写；云端 API 仅作降级兜底 |
| 结构化存储 | **按原文档方案**：MySQL（元数据）+ SQLite（会话/日志） | 需通过 Docker 运行 MySQL 8.0，建rag_meta 库 |
| 数据来源 | **两类都做**：本地 Markdown 笔记扫描 + 技术文档网站爬取 | V1 完整实现 Crawl4AI 爬取 → 清洗 → 入库链路 |
| 前端风格 | macOS 设计语言 | 对话工作台视觉按此标准执行 |
| "转人工" | 个人工具无人工可转 | 确认面板保留入口，行为简化为"记录此问题"（写入 SQLite 占位） |

---

## 2. 系统架构

经典三层架构，各层职责独立、接口解耦：

```
┌──────────────────────────────────────────────────────────┐
│                    Agent 执行层                            │
│  提问评分 → 问题改写 → 检索策略评分 → 分级检索             │
│  → RRF融合 → Rerank精排 → 结果置信度评分 → 兜底/人工确认   │
│  → 上下文组装 → LLM网关生成 → SSE流式输出                 │
├──────────────────────────────────────────────────────────┤
│                    知识处理层                              │
│  Markdown解析 → 标题感知分块 → BGE向量化                   │
│  → ChromaDB存储 → BM25索引构建                             │
├──────────────────────────────────────────────────────────┤
│                    数据采集层                              │
│  技术文档网站爬取 → HTML→Markdown转换 → 去噪清洗           │
│  本地Markdown笔记扫描 → 元数据提取 → 批量入库              │
└──────────────────────────────────────────────────────────┘
```

**技术栈**：

| 层 | 选型 |
| --- | --- |
| 后端 | Python + FastAPI（异步），LangChain 作胶水层 |
| 向量库 | ChromaDB（持久化模式，cosine） |
| 元数据 | MySQL 8.0（Docker 运行） |
| 会话/日志 | SQLite |
| Embedding | BAAI/bge-small-zh-v1.5（本地，约 90MB，CPU 可跑） |
| Rerank | BAAI/bge-reranker-v2-m3（本地 Cross-Encoder） |
| LLM | Ollama（默认）+ OpenAI 兼容协议（DeepSeek / Qwen / OpenAI 兜底） |
| 前端 | React + TypeScript + Vite，SSE 流式渲染 |
| BM25 | rank_bm25 + jieba 分词 |

**混合存储分工**：

| 存储 | 职责 |
| --- | --- |
| ChromaDB | chunk 向量、HNSW 索引、BM25 数据；按来源拆分 collection |
| MySQL | 文档元数据、chunk 映射、标签、入库任务记录 |
| SQLite | 对话历史、评分日志、反馈数据、"记录此问题"占位 |
| 文件系统 | 原始 Markdown 笔记、爬取文档、模型缓存 |

---

## 3. 功能需求（V1 范围）

### 3.1 数据采集层

#### 3.1.1 技术文档网站爬取

- 使用 **Crawl4AI**，配置目标 URL 列表与爬取深度
- 请求间隔 ≥ 2 秒，避免对目标站点造成压力
- HTML 自动转干净 Markdown，保留代码块（``` 包裹）、表格、标题层级
- 去噪：去除导航栏、侧边栏、页脚等模板内容
- 每个文档片段携带元数据：来源 URL、页面标题、爬取时间、章节路径
- 入库标记 `source_type = tech_doc`

#### 3.1.2 本地 Markdown 笔记扫描

- `pathlib` 递归扫描目标目录，收集所有 `.md` 文件
- 标题感知拆分：按 `#` `##` `###` 层级识别内容边界，最小标题块为一个语义单元；单元超限时按段落、句子递归拆分
- 保留标题路径（如 "RAG > 检索策略 > 混合检索"）作为元数据，检索结果中展示
- 元数据字段：`source_type`（tech_doc / personal_note）、`file_path`、`heading_path`、`created_at`、`updated_at`、`tags`
- 入库标记 `source_type = personal_note`

### 3.2 知识处理层

#### 3.2.1 分块策略（标题感知 + 递归拆分）

- 按 `#`~`####` 拆分为语义块；超过 `chunk_size` 的块再用 RecursiveCharacterTextSplitter 递归切分
- 参数：`chunk_size=512`（约 300-400 中文字符）、`chunk_overlap=64`
- 中文分隔符优先级：段落 > 换行 > 中文句号 > 分号 > 逗号
- 每块元数据：`heading_path`、`chunk_index`、`total_chunks` 及文件级元数据

#### 3.2.2 向量化与 ChromaDB 存储

- 本地 BGE-small-zh-v1.5 向量化，无需 API 费用
- `chromadb.PersistentClient` 持久化，`hnsw:space=cosine`
- 批量入库，每批 100 条；chunk 大小远低于 Chroma 单条 16KB 限制
- **多集合隔离**：按来源拆分 collection（`tech_docs` / `personal_notes`），检索时先按来源过滤再检索

#### 3.2.3 MySQL 元数据表

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `documents` | file_path(唯一)、source_type、title、heading_path、content_hash(SHA-256)、chunk_count、created_at、updated_at、last_ingested | 文档主表，哈希用于变更检测 |
| `chunks` | document_id(外键级联)、chunk_index、chroma_id(唯一)、heading_path、token_count | 与 ChromaDB 向量的映射 |
| `tags` / `document_tags` | name(唯一)；document_id + tag_id 联合主键 | 标签体系 |
| `ingest_tasks` | task_id(唯一)、status(pending/processing/completed/failed)、file_count、chunk_count、error_message | 入库任务追踪 |

均使用 InnoDB + utf8mb4，建立 source_type / updated_at / content_hash / document 索引。

#### 3.2.4 增量入库与去重

1. 读取文件计算 SHA-256 内容哈希
2. 查 MySQL 已有记录：哈希相同 → 跳过
3. 有变更 → 删除旧向量（按 chroma_id）→ 删除旧元数据 → 重新分块向量化 → 写入新元数据
4. 支持 `force_reingest` 参数强制全量重入

#### 3.2.5 存储抽象层

- 定义 `VectorStore`（add_chunks / query / delete_by_ids / delete_by_metadata）与 `MetadataStore`（upsert_document / get_document / list_documents / delete_document）抽象接口
- V1 只实现 ChromaDB 与 MySQL 实现；抽象层为未来迁移 Qdrant 预留，业务代码不感知底层

### 3.3 Agent 执行层

#### 3.3.1 提问信息评分（规则引擎，不调 LLM）

五维度各 20 分，满分 100：

| 维度 | 评分要点 |
| --- | --- |
| 意图明确度 | 无未消解指代词满分；含指代词但可从历史消解 15 分；指代不明 5 分 |
| 信息完整度 | 含版本号/环境/功能名等限定满分；可推测 12 分；完全模糊 4 分 |
| 领域相关性 | 命中知识库领域词表 ≥2 满分；命中 1 个或用户历史高频标签 12 分；无关 0 分 |
| 问题复杂度 | 单一问题满分；2 个独立问题 12 分；≥3 个或逻辑混乱 4 分 |
| 安全与合规 | 含越狱/攻击词 0 分，否则满分 |

辅助信息：最近 3 轮对话历史 + MySQL 中该用户近 7 天高频查询标签。

**阈值与动作**：

| 总分 | 判定 | 动作 |
| --- | --- | --- |
| ≥ 80 | 自动放过 | 直接进入检索流程 |
| 60 ~ 79 | 建议澄清 | 先返回澄清提示，用户确认后检索；坚持则降级处理 |
| < 60 | 人工确认 | 不检索，SSE 推送 confirm 事件，前端弹确认框 |

#### 3.3.2 多轮问题改写

- 基于最近 3 轮历史，将指代性问题（"那价格呢""这个怎么配置"）改写为独立完整查询
- 使用轻量模型（qwen2.5:3b），保留原始意图、补全指代，只输出改写后语句
- 无历史时不改写

#### 3.3.3 检索策略评分与分级检索

**关键字权重表**（可从知识库元数据/标题/高频术语自动构建）：

| 类型 | 示例 | 权重 |
| --- | --- | --- |
| 精确 API/类名、错误码 | `BM25Okapi`、`TimeoutError` | +30 |
| 框架/工具名 | `ChromaDB`、`FastAPI` | +20 |
| 功能/概念 | `混合检索`、`RRF融合` | +15 |
| 版本/环境限定 | `v0.4.0`、`Python 3.11` | +10 |
| 通用技术词 | `配置`、`安装` | +5 |
| 模糊词 | `一些`、`怎么弄` | -10 |

公式：`原始分 + 长度调节(8~50字 +10) + 指代惩罚(-15)`，clamp 到 0~100。

**检索级别**：

| 分数 | 级别 | 策略 | 参数 |
| --- | --- | --- | --- |
| ≥ 80 | L3 | 混合检索 + RRF + Rerank | 向量30 + BM25 30 → RRF 取 20 → Rerank → top 5 |
| 60 ~ 79 | L2 | 混合 + RRF，无 Rerank | 向量20 + BM25 20 → RRF 取 8 |
| 40 ~ 59 | L1 | 纯向量 | top 10，相似度阈值 0.5 |
| < 40 | L0 | 兜底 | 见 3.3.4 |

**混合检索参数**：RRF k=60（标准值）；向量权重 1.0 / BM25 权重 0.7；查询与文档均经 jieba 分词；Rerank 将 Top 20 候选逐一与 Query 配对送 Cross-Encoder 重打分。

**降级触发**：结果为空 / Rerank 最高分 < 0.3（L3）/ 向量相似度最高分 < 0.45（L1/L2）/ 检索异常 → 自动切换下一级，L3 → L2 → L1 → L0。

#### 3.3.4 结果置信度与兜底

**置信度计算**：L3 取 rerank 最高分；L2 取 RRF 分数经验归一化（max/0.05，上限 1.0）；L1 取向量相似度最高分。

**阈值动作**：

- ≥ 0.6：高置信，直接生成答案
- 0.3 ~ 0.6：中置信，生成答案但附加"以下回答可能不完整"提示
- < 0.3：低置信，触发降级或兜底

**Level 0 兜底分支**（按问题类型分流）：

- 通用技术常识：允许 LLM 基于自身知识回答，**开头必须声明"以下回答未基于本地知识库，仅供参考"**
- 与知识库强相关但未检索到：直接回复"未在现有文档中找到相关内容，请补充关键词"
- 敏感/越狱问题：直接拒答"抱歉，我无法处理该请求"

**禁止行为（防幻觉硬约束）**：

- 不得编造文档中不存在的来源引用
- 不得在未检索到内容时假装引用了文档
- 不得对低置信结果进行"脑补"式扩写

**防报错兜底**：

- 所有检索调用包裹 try/except，异常自动降级
- LLM 调用设超时与重试（最多 2 次），失败返回友好错误
- ChromaDB 查询失败回退 BM25 内存索引
- SSE 增加 `event: error`，携带错误码与建议

#### 3.3.5 人工确认交互

**触发条件**：提问评分 < 60；评分 60~79 且用户选择澄清；检索置信度 < 40 且问题强相关；连续两轮置信度 < 0.3。

**流程**：

1. 后端不生成答案，SSE 推送 `confirm` 事件（原始问题、改写后问题、选项）
2. 前端弹出确认面板，用户可选：**确认 / 修改 / 记录此问题**
3. 确认 → 用改写后问题重入检索流程，跳过提问评分
4. 修改 → 编辑问题后重新提交
5. 记录此问题 → 写入 SQLite 留档（原"转人工"的简化占位），返回确认文案

#### 3.3.6 答案生成与来源引用

- 两阶段提示词：先生成思考过程，再拼入 Prompt 生成最终答案
- System Prompt 约束：严格基于检索片段回答；用 `[来源N]` 标注来源；内容不足时明确说"根据现有文档无法确定"；不编造；Markdown 格式输出
- Context 组装携带 `file_path` 与 `heading_path`，供引用展示
- 流式输出（SSE），支持中断

### 3.4 LLM 统一网关

#### 3.4.1 统一接口

`chat() / stream() / embed() / rerank()`，业务层不感知底层 Provider 差异。

#### 3.4.2 Provider 适配层

- `OllamaProvider`：Ollama 原生 SDK，本地默认
- `OpenAICompatibleProvider`：统一适配 DeepSeek / Qwen（百炼）/ OpenAI，仅替换 base_url、api_key、model
- 各 Provider 实现 `is_available()` 健康检查

#### 3.4.3 模型路由与降级

`config/models.yaml` 按任务类型配置优先级链，**链上靠前的失败自动降级到下一个**：

| 任务类型 | 默认链（V1） | 说明 |
| --- | --- | --- |
| generate 答案生成 | Ollama qwen2.5:7b → DeepSeek → OpenAI | 质量要求高，允许降级到云端 |
| rewrite 问题改写 | Ollama qwen2.5:3b → DeepSeek | 轻量任务 |
| embedding | 本地 bge-small-zh →（可选云端） | CPU 可跑 |
| rerank | 本地 bge-reranker-v2-m3 | Cross-Encoder 精排 |

- API Key 一律走 `.env` 环境变量（`${VAR}` 占位解析），不入库、不进前端
- 全链失败抛出友好错误（AllProvidersFailedError）
- RAG 服务中原有直接调用 Ollama 的代码统一替换为网关调用

#### 3.4.4 前端模型元信息

SSE 事件增加 `meta` 事件，告知前端当前使用的 model / provider / task_type。

### 3.5 FastAPI 后端

#### 3.5.1 项目结构

```
backend/
├── app/
│   ├── main.py                  # 应用入口
│   ├── config.py                # Pydantic Settings 配置管理
│   ├── routers/
│   │   ├── chat.py              # 对话接口（SSE 流式）
│   │   ├── documents.py         # 文档管理接口
│   │   └── health.py            # 健康检查
│   ├── services/
│   │   ├── rag_service.py       # RAG 检索增强服务
│   │   ├── doc_processor.py     # 文档解析与向量化
│   │   ├── llm_gateway.py       # LLM 统一网关
│   │   ├── vector_store.py      # ChromaDB 管理
│   │   └── metadata_store.py    # MySQL 元数据管理
│   ├── providers/
│   │   ├── base.py / ollama_provider.py / openai_provider.py
│   ├── core/
│   │   ├── retriever.py         # 混合检索器
│   │   ├── query_rewriter.py    # 多轮问题改写
│   │   ├── question_scorer.py   # 提问信息评分
│   │   └── retrieval_scorer.py  # 检索策略评分
│   └── models/schemas.py        # Pydantic 请求/响应模型
├── data/chroma_db/ · data/notes/
├── config/models.yaml
├── requirements.txt
```

#### 3.5.2 核心接口

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/documents/ingest` | POST | 扫描目录异步入库；参数 directory / source_type / force_reingest；返回 task_id、file_count |
| `/api/documents/status/{task_id}` | GET | 查询入库进度 |
| `/api/chat/stream` | POST | SSE 流式对话；参数 question / session_id / top_k / model_id |
| `/api/models` | GET | 可用模型列表（供前端模型选择器，含 available 状态） |
| `/api/health` | GET | 健康检查 |

**SSE 事件协议**（类型化事件）：

| 事件 | 时机 | 载荷 |
| --- | --- | --- |
| `meta` | 开始 | model / provider / task_type |
| `sources` | 检索完成后 | 来源数组（index、file_path、heading_path、excerpt、score） |
| `content` | 流式生成中 | 增量文本 |
| `confirm` | 触发人工确认 | original_question / rewritten_question / options |
| `error` | 出错 | 错误码与建议 |
| `done` | 结束 | session_id |

对话主流程：提问评分 →（<60 走 confirm 并终止）→ 问题改写 → 检索评分定级 → 带降级的分级检索 → 置信度判定 →（低置信走兜底）→ sources → 流式生成 → done。

### 3.6 React 前端

#### 3.6.1 组件结构

```
frontend/src/
├── App.tsx
├── components/
│   ├── ChatContainer.tsx        # 对话容器
│   ├── MessageBubble.tsx        # 消息气泡（含引用链接）
│   ├── CitationPanel.tsx        # 来源面板（侧滑）
│   ├── ChatInput.tsx            # 输入框（自动伸缩）
│   ├── SourceCard.tsx           # 单条来源卡片
│   ├── ConfirmDialog.tsx        # 人工确认面板
│   └── ModelSelector.tsx        # 模型选择器
├── hooks/useChatStream.ts       # SSE 流式 Hook
├── types/chat.ts
└── api/client.ts
```

#### 3.6.2 关键交互

- **SSE 流式 Hook**：解析类型化事件；打字机效果；停止生成（AbortController）；confirm/error/done 状态处理
- **来源引用**：回答中 `[N]` 行内标注，点击联动来源面板高亮对应条目；来源卡片展示文件路径、章节路径、摘录（前 200 字）、分数；显示"使用了 N 个来源"徽标
- **人工确认面板**：展示原始问题与系统改写结果，支持编辑后提交；按钮为 确认 / 修改 / 记录此问题
- **模型选择器**：下拉切换模型，不可用项置灰禁用；展示"本地/云端"标识
- **Markdown 渲染**：marked + DOMPurify + highlight.js，代码高亮 + XSS 防护
- **UI 风格**：macOS 设计语言（参考 macOS Weather App 的 Liquid Glass 材质与空间层次）
- **会话管理**：session_id 贯穿前后端，对话历史持久化到 SQLite

---

## 4. 非功能需求

| 项 | 要求 |
| --- | --- |
| 硬件基线 | 16GB+ 内存 / 有独显；qwen2.5:7b (Q4) 预期查询延迟 1~2 秒 |
| 数据规模预期 | 个人知识库 chunk 数 1 万~10 万，ChromaDB 在此范围内可用 |
| 成本 | 默认本地模型，云端仅兜底，月度 API 成本控制在低水位 |
| 安全 | API Key 仅存 `.env`；前端渲染过 DOMPurify；敏感/越狱问题拒答 |
| 可维护性 | VectorStore / MetadataStore 抽象接口隔离底层；LLM 调用统一走网关 |

---

## 5. 版本范围

### 5.1 V1 必须完成

1. 数据采集：Crawl4AI 网站爬取 + 本地 Markdown 扫描，标题感知分块
2. 知识处理：BGE 本地向量化 → ChromaDB（多集合）；MySQL 元数据（哈希去重、增量入库）；SQLite 会话
3. Agent 执行层：提问评分（含人工确认）、多轮改写、检索评分、四级检索降级、置信度兜底
4. LLM 网关：Ollama 优先 + OpenAI 兼容云端兜底，models.yaml 路由
5. FastAPI 后端：入库接口、SSE 流式对话（六类事件）
6. React 前端：对话工作台、来源引用面板、确认弹窗、模型选择器、打字机流式、macOS 风格
7. 本地开发环境联调跑通（Ollama + Docker MySQL + 后端 + 前端）

### 5.2 V1 明确不做（后续版本）

| 项 | 原文档出处 | 后置理由 |
| --- | --- | --- |
| 评估体系（50-100 条评估集、CI 四类指标卡控） | 9.1 | 需先有稳定 V1 与真实使用数据 |
| 性能优化（embedding 缓存、BM25 增量更新、批量入库调优） | 9.2 | 数据量小，暂无瓶颈 |
| 知识图谱集成 | 9.3 | 进阶功能 |
| 多知识库隔离 | 9.3 | 单人多库需求未出现 |
| 定时爬取同步 | 9.3 | 手动触发入库已够用 |
| 评分反馈闭环 | 9.3 | 需积累反馈数据 |
| ChromaDB 运维（索引重建、量化压缩、Qdrant 迁移评估） | 8.3/8.4 | 数据量远未到阈值；抽象层已预留 |
| Docker Compose 一键部署 | 7.2 | V1 先开发模式跑通 |
| "转人工"完整流程 | 4.7 | 个人工具，简化为"记录此问题"占位 |
| Provider 调用统计与费用面板 | 4.8.6 | 模型切换保留，统计后置 |

---

## 6. 运行环境（开发模式）

```bash
# 1. Ollama 拉取模型
ollama pull qwen2.5:7b
ollama pull qwen2.5:3b

# 2. Docker 启动 MySQL
docker run -d --name rag-mysql \
  -e MYSQL_ROOT_PASSWORD=rag123 \
  -e MYSQL_DATABASE=rag_meta \
  -p 3306:3306 \
  mysql:8.0

# 3. 后端
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 4. 前端
cd frontend
npm install
npm run dev   # http://localhost:5173
```

---

## 7. 验收标准

| # | 场景 | 预期 |
| --- | --- | --- |
| 1 | 本地笔记目录首次入库 | 全部文件分块向量化成功，MySQL 元数据完整 |
| 2 | 重复执行入库 | 哈希未变的文件全部跳过 |
| 3 | 修改一个 md 后再入库 | 仅该文件删除旧向量并重新入库 |
| 4 | 提出含 API 名的明确问题 | 走 L3 完整链路，答案带 [来源N] 标注，来源面板可点击联动 |
| 5 | 提出模糊问题（如"这个怎么配？"） | 弹出确认框展示改写结果，确认/修改/记录均可用 |
| 6 | 提出知识库中不存在的问题 | 回复"未找到相关内容"，不编造引用 |
| 7 | 提出通用技术常识问题 | 可回答，但开头声明"未基于本地知识库" |
| 8 | 停掉 Ollama 服务 | 自动降级到配置的云端模型继续可用（已配置时） |
| 9 | SSE 流式输出 | 打字机效果正常，可中途停止，error 事件有友好提示 |
| 10 | 网站爬取入库 | 指定 URL 爬取转 Markdown 成功，代码块/表格结构保留 |

---

## 附录 B：V1.1 来源引用跳转优化方案

> 触发日期 2026-10-19 · 状态：已确认方案，待实施
> 优化目标：来源面板 `SourceCard` 点击后能正确打开原始出处
> 覆盖范围：后端 metadata 链路打通 + 新增文件打开接口 + 前端 SourceCard 重构

### B.1 问题背景

V1 的来源面板只展示"文件绝对路径 / 章节 / 片段摘要"，点击无任何跳转行为。根本原因：

| 环节 | 字段 | 现状 | 问题 |
| --- | --- | --- | --- |
| 爬虫落盘 | front-matter | ✅ 写了 `source_url` | 但后续链路没透传 |
| `scan_markdown_files` | 返回结构 | 只读 title/tags/时间 | 漏掉 `source_url` |
| chunk metadata | 写入 Chroma | 有 `file_path` / `source_type` / `title` / `heading_path` | 缺 `source_url` |
| `build_sources` | SSE sources 下发 | 返回 `{index, file_path, heading_path, excerpt, score}` | 缺 url / source_type / title |
| 前端 `SourceItem` | TS 类型 | 同上 | 缺 url 等 |
| 前端 `SourceCard` | 渲染 | 原样显示绝对路径（纯文本） | 不可点 |

浏览器 http 页面不能直接 `window.open("file://...")` 打开本地文件（Chrome/Edge 安全策略拦截），所以本地 md 必须由后端帮忙调起默认应用。

### B.2 优化目标

1. **网页来源可跳转**：`source_type = tech_doc` 且存在 `source_url` 时，点击 SourceCard → 新标签打开原始网页
2. **本地 md 可打开**：`source_type = personal_note`（或 tech_doc 无 url）时，点击 SourceCard → 后端 `os.startfile()` 用默认应用打开 md
3. **两种来源混合场景**：同一轮回答同时有网页与本地来源时，各自点击行为独立生效
4. **安全约束**：后端打开接口限制只能打开 `data/notes/` 与 `data/tech_docs/` 下的 `.md` 文件

### B.3 后端改动

#### B.3.1 metadata 链路补齐 `source_url`

| 文件 | 改动 |
| --- | --- |
| `backend/app/services/note_scanner.py:scan_markdown_files` | 返回结构加 `"source_url": str(fm.get("source_url") or "")` |
| `backend/app/services/ingest_pipeline.py:prepare_chunks` | chunk metadata 加 `"source_url": file_record.get("source_url") or ""` |
| `backend/app/services/ingest_pipeline.py:upsert_document` | 传入 `source_url` |

> MySQL `documents` 表已有 `source_url TEXT NULL` 列（PRD 3.2.3 定义），无需改表结构。

#### B.3.2 `build_sources` / `build_context` 补全字段

```python
def build_sources(chunks: list[dict]) -> list[dict]:
    return [
        {
            "index": i + 1,
            "title": c.get("metadata", {}).get("title", ""),
            "file_path": c.get("metadata", {}).get("file_path", "未知"),
            "heading_path": c.get("metadata", {}).get("heading_path", ""),
            "source_type": c.get("metadata", {}).get("source_type", ""),
            "source_url": c.get("metadata", {}).get("source_url", ""),
            "excerpt": c["text"][:200],
            "score": float(c.get("rerank_score", c.get("score", 0.0)) or 0.0),
        }
        for i, c in enumerate(chunks)
    ]
```

`build_context` 给 LLM 的文件引用也带上 `source_url`，提升回答里 `[来源N]` 标注的可靠性。

#### B.3.3 新增 `/api/files/open` 接口

建议单独建 `backend/app/routers/files.py`：

```python
from fastapi import APIRouter, HTTPException, Query
from pathlib import Path
import os

router = APIRouter(prefix="/api", tags=["files"])

ALLOWED_ROOTS = ["data/notes", "data/tech_docs"]  # 白名单

@router.get("/files/open")
async def open_local_file(path: str = Query(..., description="绝对路径")):
    p = Path(path).resolve()
    if not p.exists():
        raise HTTPException(404, f"文件不存在: {path}")
    if p.suffix.lower() != ".md":
        raise HTTPException(400, "只允许打开 .md 文件")
    backend_root = Path(__file__).parent.parent.resolve()
    ok = any(str(p).startswith(str(backend_root / r).replace("/", os.sep)) for r in ALLOWED_ROOTS)
    if not ok:
        raise HTTPException(403, "路径不在允许范围内")
    try:
        if os.name == "nt":
            os.startfile(str(p))  # Windows 用默认应用打开
        else:
            import webbrowser
            webbrowser.open(p.as_uri())
    except Exception as e:
        raise HTTPException(500, f"打开失败: {e}")
    return {"ok": True, "path": str(p)}
```

#### B.3.4 重入库

修改代码链路后，**已入库的 chunk metadata 不含 `source_url`**。执行一次 force_reingest 补齐：

```bash
curl -X POST http://localhost:8000/api/documents/ingest \
     -H 'Content-Type: application/json' \
     -d '{"directory":"data/notes","source_type":"personal_note","force_reingest":true}'
curl -X POST http://localhost:8000/api/documents/ingest \
     -H 'Content-Type: application/json' \
     -d '{"directory":"data/tech_docs","source_type":"tech_doc","force_reingest":true}'
```

### B.4 前端改动

#### B.4.1 类型升级

```ts
// frontend/src/types/chat.ts
export interface SourceItem {
  index: number;
  title: string;          // 新增：简洁标题，优先显示
  file_path: string;
  heading_path: string;
  source_type: string;    // "tech_doc" | "personal_note"
  source_url: string;     // 新增：爬虫文档原始 URL（可为空）
  excerpt: string;
  score: number;
}
```

#### B.4.2 SourceCard 重构

**渲染层级（从上到下）**：

```
[1]  FastAPI 依赖注入笔记          ← title（优先）或文件名 stem
    来源: 本地笔记 📄              ← source_type 徽章（两色区分）
    或  来源: 网页文档 ↗ 点击跳转  ← tech_doc 且有 source_url 时显示
    D:\...\ollama_ops.md           ← 完整文件路径（灰色小字）
    / 使用 Depends / 常见坑         ← heading_path
    片段摘要...                    ← excerpt
```

**点击分支**：

```ts
const handleClick = () => {
  if (source.source_type === "tech_doc" && source.source_url) {
    // 网页来源 → 新标签打开原始文章
    window.open(source.source_url, "_blank", "noopener,noreferrer");
  } else {
    // 本地 md 或兜底 → 让后端用默认应用打开
    fetch(`/api/files/open?path=${encodeURIComponent(source.file_path)}`);
  }
};
```

样式：可点击区域 `cursor: pointer` + hover 背景色；右下角加视觉图标（↗ 网页 / 📄 文件）。

### B.5 改动文件清单

| 文件 | 操作 |
| --- | --- |
| `backend/app/services/note_scanner.py` | 改（scan_markdown_files 返回加 source_url） |
| `backend/app/services/ingest_pipeline.py` | 改（prepare_chunks + upsert_document 传递 source_url） |
| `backend/app/services/rag_service.py` | 改（build_sources + build_context 补字段） |
| `backend/app/routers/files.py` | 新建（/api/files/open 接口） |
| `backend/tests/` | 补 3 个后端单测（链路字段完整性 + open 接口白名单校验） |
| `frontend/src/types/chat.ts` | 改 SourceItem |
| `frontend/src/components/SourceCard.tsx` | 改（渲染 + 点击分支） |
| `frontend/src/styles.css` | 加徽章/图标样式 |
| `frontend/src/components/SourceCard.test.tsx` | 新建 vitest（渲染分支 + 点击行为分支） |

### B.6 验收标准（每条可测）

| # | 场景 | 预期 |
| --- | --- | --- |
| 1 | 网页来源点击 | tech_doc 且有 source_url → 浏览器新标签打开原始网页 |
| 2 | 本地 md 点击 | personal_note → 本机用默认应用打开 md |
| 3 | 混合来源同答 | 同一轮回答同时有网页和本地来源 → 两种点击行为各自生效 |
| 4 | 安全拦截 | 用 Postman 调 `/api/files/open?path=C:/Windows/System32/config/SAM` → 返回 403 |
| 5 | 未知类型兜底 | source_type 缺失 → 降级走后端 open 接口，兜底能开 |
| 6 | 已有对话不崩 | 新增字段均为可选默认值，历史对话 SourceCard 正常渲染 |

### B.7 关键设计决策

| 问题 | 决策 | 理由 |
| --- | --- | --- |
| 前端能否直接 `window.open(file://...)` | ❌ 不行 | 现代浏览器 http 页面被禁用 file:// 协议跳转，必须后端调起 |
| `/api/files/open` 安全风险 | 可控 | 加白名单只允许 `data/notes/` 与 `data/tech_docs/` 下的 `.md`，禁止任意文件 |
| title 从哪来 | chunk metadata 已有 title | prepare_chunks 写入，不用额外查库 |
| 爬虫文档是否必须重入库 | 是 | 现有 Chroma chunk metadata 不含 source_url，force_reingest 一次补齐；后续新入库自动带上 |
| 老对话历史 sources 能否升级 | 不行也不需要 | 老 sources 已落 SQLite 保持原样，新对话自动用新字段 |

---

## 附录 C：V1.2 来源引用合并（同一文档多片段合并为一张卡片）

> 触发日期 2026-10-19 · 状态：已实施并验收
> 优化目标：来源面板中同一文档的多个检索片段合并为一张卡片，同时保证正文 `[来源N]` 点击联动仍然精确
> 覆盖范围：后端 `build_sources` 分组 + 前端 `SourceCard` / `CitationPanel` 展示重构 + 正文引用识别补全

### C.1 问题背景

提问「Python 学习路线」时，面板显示 10 个"来源"，但实际只来自 2 篇文档：同一 html 的 3.1/3.2/3.4/3.5 等多个片段被各渲染成一张卡片，标题、文件路径甚至分数完全相同，用户无法分辨。

| 环节 | 现状 | 问题 |
| --- | --- | --- |
| `build_sources` | 一个 chunk 一条记录，只做字段搬运 | 无分组逻辑，片段数即卡片数 |
| `CitationPanel` | `sources.map(...)` 直接渲染 | 计数口径是"片段"而非"文档" |
| 正文引用 `[来源N]` | N 是送进 LLM context 的片段序号 | 合并展示时若重排 N 会导致引用错位 |

### C.2 合并规则

| 规则 | 定义 |
| --- | --- |
| 合并键 | `(source_type, normalized_file_path)`，路径归一化 = `\`→`/` + 去首尾空白 + 大小写折叠 |
| 代表 index | 组内最小片段序号 |
| indices | 组内全部片段序号（升序），**并集必须严格等于 1..N** |
| score | 组内 max（代表该文档最佳相关度，并据此降序排列） |
| headings | 组内 `heading_path` 去重保序 |
| excerpts | 按序号升序取摘要，后端最多 3 条 |
| chunk_count | `len(indices)` |
| 边界 | 缺 `file_path` 的片段单独成组；同路径但 `source_type` 不同不合并 |

### C.3 后端改动

- `backend/app/services/rag_service.py`：新增 `MAX_EXCERPTS`、`_normalize_path`、`_group_key`、`merge_chunks_to_sources`；`build_sources` 改为调用合并函数。**`build_context` 一字未改**（context 顺序与编号不变）。
- SSE `sources` 事件字段由 8 项扩为 12 项，新增 `indices` / `chunk_count` / `headings` / `excerpts`；`index` / `heading_path` / `excerpt` 保留为兼容字段。
- 新增 `backend/tests/test_build_sources_merge.py`（11 条），更新 `test_rag_service_fields.py`（字段集 12 项）、`test_main.py`（合并与 indices 覆盖）、`test_session_store.py`（合并结构 json 往返）。

### C.4 前端改动

- `SourceItem` 增加可选 `indices` / `chunk_count` / `headings` / `excerpts`（旧数据自动降级为单片段）。
- `SourceCard`：根节点由 `<button>` 改为 `div[role=button]`（避免内嵌"展开"按钮的非法嵌套），新增「共 N 段」徽章、多章节列表、多摘要折叠（默认 2 条 + 「展开剩余 K 段」）。
- `CitationPanel`：副标题改为「使用了 X 篇文档 / Y 个片段」；高亮判定改为 `indices.includes(activeIndex)`。
- `MessageBubble`：引用识别正则由 `/\[(\d{1,2})\]/` 扩为 `/\[(?:来源\s*)?(\d{1,2})\]/`。实机复核发现本地模型按 SYSTEM_PROMPT 实际输出的是 `[来源N]` 字面量，旧正则只认 `[N]`，导致引用按钮根本不出现。
- 新增 `CitationPanel.test.tsx`（6 条）、`useChatStream.test.tsx`（1 条），`SourceCard.test.tsx` 扩 5 条、`MessageBubble.test.tsx` 扩 2 条。

### C.5 验收结果

| # | 验收项 | 结果 |
| --- | --- | --- |
| 1 | 同一文档多片段 → 面板只显示 1 张卡片，章节齐全，摘要可展开 | ✅ 实机：10 片段 → 2 篇文档，每张「共 5 段」 |
| 2 | 点击正文 `[来源N]`（任意 N ∈ indices）→ 面板打开且同一张卡片高亮 | ✅ 实机：点 [5] 仅包含 5 的那张卡高亮；换序号高亮正确切换 |
| 3 | 计数双口径：面板「使用了 X 篇文档 / Y 个片段」 | ✅ 实机：2 篇/10 片段、3 篇/5 片段两种场景均正确 |
| 4 | 不同文档不误合并；跨 source_type、空 file_path 不合并 | ✅ 单测覆盖 |
| 5 | indices 并集严格等于 1..N 且无重复 | ✅ 单测 + 实机并集 `[1..10]` 完全覆盖 |
| 6 | 网页跳转 / 本地 md 打开行为与 V1.1 一致 | ✅ 既有 14 条 SourceCard 用例全绿 |
| 7 | 全量回归：后端 pytest、前端 vitest、tsc、vite build | ✅ 114 / 45 / 0 error / 0 error |

### C.6 关键设计决策

| 决策 | 结论 | 理由 |
| --- | --- | --- |
| 合并发生在展示层还是检索层 | 展示层（`build_sources` 产出聚合卡片） | 改检索层会改变送进 LLM 的 context，影响答案质量与引用编号 |
| 合并后是否重排片段序号 | 不重排 | `[来源N]` 直接对应 context 片段序号，重排必然引用错位 |
| 卡片如何精确联动 | 卡片携带 `indices`，前端用 `indices.includes(activeIndex)` 判定高亮 | 展示合并与引用精确性两者兼得 |
| 前端字段是否设为必填 | 全部可选并保留兼容字段 | 老会话历史与旧 SSE payload 无需迁移即可渲染 |
| 摘要展示上限 | 后端最多 3 条 + 前端默认折叠 2 条 | 兼顾信息量与卡片高度 |

---

## 附录 D：V1.3 来源面板四项优化（路径合并 / 隐藏地址 / 去标题行 / hybrid 双入口）

### D.1 背景与问题

V1.2 完成了「同一文档多片段合并」，但实机使用中仍存在以下体验问题：

1. **同文档因路径表示不同而漏合并**：同一 `chromadb_usage.md` 入库时分别以绝对路径（`D:\...\backend\data\notes\chromadb_usage.md`）和相对路径（`data\notes\chromadb_usage.md`）出现，被判定为两个文档，渲染出两张重复卡片。
2. **明文地址暴露**：卡片直接展示完整本地路径（`D:\...\chromadb_usage.md`）和网页 URL，信息冗余且不美观。
3. **摘要首行重复章节标题**：分片文本以 markdown 标题行（如 `## 集合与持久化`）开头，导致摘要首行与卡片标题/章节重复。
4. **本地笔记与网络文章为同一文档时缺少双入口**：当 personal_note 与 tech_doc 实际是同一篇内容时，用户需要既能打开本地 md，又能跳转网页原文，但 V1.2 只提供单一跳转。

### D.2 优化目标

| # | 目标 | 验收标准 |
| --- | --- | --- |
| 1 | 路径归一化合并 | 绝对路径与相对路径指向同一文件时合并为一张卡 |
| 2 | 隐藏明文地址 | 卡片不渲染完整本地路径与网页 URL，仅显示文件名 |
| 3 | 剥离摘要标题行 | excerpt 不以 `#` 开头的 markdown 标题行起始 |
| 4 | hybrid 双入口 | 本地+网络同文档合并为一张卡，提供「本地打开」与「网页原文」两个按钮，本地优先 |

### D.3 后端实现

**`backend/app/services/rag_service.py`**

1. **`_normalize_doc_path`**：归一化路径时剥离 `data/` 前缀，使绝对路径与相对路径落到同一 key。
   - 取路径中最后一个 `/data/` 之后的部分；若以 `data/` 开头则剥离该前缀。
   - 统一反斜杠转正斜杠、去首尾空白、casefold。

2. **`_normalize_title`**：标题归一化，剥离首尾空白（含全角空格）并 casefold，用于跨类型合并匹配。

3. **`_strip_heading_lines`**：摘要预处理，移除以 `#` 开头的 markdown 标题行（含 `##`、`###` 等），保留正文。

4. **两轮合并**：
   - 第一轮：按 `(source_type, normalized_path)` 分组（同 V1.2 路径合并）。
   - 第二轮：跨 `source_type` 按归一化标题合并——当一张 `personal_note` 卡与一张 `tech_doc` 卡标题相同时，合并为 `is_hybrid: true` 卡：
     - `source_type = "personal_note"`（本地优先）
     - `file_path` 取本地笔记路径
     - `source_url` 取网页文档 URL
     - `indices` 取两者并集
     - 仅 `personal_note × tech_doc` 组合触发；同类型不按标题合并（避免误合并不同文档）。

### D.4 前端实现

**`frontend/src/components/SourceCard.tsx`**

1. **隐藏明文地址**：`source-path` 只渲染 `file_path` 的 basename（`split(/[\\/]/).pop()`），不再展示完整路径；移除 `title` tooltip 中的路径/URL。
2. **移除章节列表**：不再渲染 `headings` 章节列表（与标题/摘要重复，节省卡片高度）。
3. **hybrid 双按钮**：当 `is_hybrid === true` 时：
   - 渲染「📄 本地打开」（primary，调 `/api/files/open`）与「↗ 网页原文」（调 `window.open`）两个按钮；
   - 卡片整体点击**不触发跳转**（避免与显式按钮歧义），仅保留父组件的激活回调。
4. **非 hybrid 卡**：保持 V1.2 行为（卡点击 → 本地打开或网页跳转）。

**`frontend/src/types/chat.ts`**：`SourceItem` 新增 `is_hybrid?: boolean`。

### D.5 测试覆盖

**后端** `backend/tests/test_build_sources_merge.py`（17 条）：

| 测试 | 验证点 |
| --- | --- |
| `test_absolute_and_relative_path_merge_to_same_doc` | 绝对+相对路径合并为一卡，indices 覆盖 |
| `test_hybrid_local_and_web_same_title_merges` | personal_note + tech_doc 同标题 → hybrid，本地优先，双跳转字段齐全 |
| `test_hybrid_title_normalization_ignores_whitespace_and_case` | 标题归一化忽略空白/大小写/全角空格 |
| `test_two_tech_docs_same_title_not_merged` | 同类型 tech_doc 同标题不合并 |
| `test_excerpt_strips_markdown_heading_lines` | 摘要剥离 `##` 标题行 |
| `test_non_hybrid_cards_have_no_is_hybrid_flag` | 非 hybrid 卡不带 `is_hybrid` 字段 |

**前端** `frontend/src/components/SourceCard.test.tsx`（21 条）：

| 测试 | 验证点 |
| --- | --- |
| 不渲染完整本地路径，仅显示文件名 | basename 展示，完整路径不在 DOM |
| 不渲染网页 URL 明文 | 无 `http(s)://` 文本 |
| 合并卡片不再渲染章节列表 | headings 文本不出现在 DOM |
| hybrid 渲染「本地打开」与「网页原文」双按钮 | 两按钮均存在 |
| hybrid 本地按钮调用 `/api/files/open` | fetch 调用路径正确，不调 window.open |
| hybrid 网页按钮调用 `window.open` | 打开 source_url，不调 fetch |
| hybrid 卡片整体点击不触发跳转 | 避免与按钮歧义 |

### D.6 验收结果

| # | 验收项 | 结果 |
| --- | --- | --- |
| 1 | 同文档绝对/相对路径合并为一卡 | ✅ 实机「ChromaDB 使用要点」1 张卡 |
| 2 | 无明文路径/URL，仅显示文件名 | ✅ 仅显示 `chromadb_usage.md` 等 basename |
| 3 | 摘要不以 markdown 标题行起始 | ✅ 摘要首行为正文内容 |
| 4 | 引用点击联动来源卡片高亮 | ✅ 点击 [来源N] 卡片激活 |
| 5 | 后端 pytest 全绿 | ✅ 120 passed |
| 6 | 前端 vitest 全绿 | ✅ 52 passed |
| 7 | tsc / vite build 零错误 | ✅ 0 error / built in 668ms |

### D.7 关键设计决策

| 决策 | 结论 | 理由 |
| --- | --- | --- |
| 路径归一化是否剥离 `data/` 前缀 | 是 | 入库时相对路径带 `data/`、绝对路径含完整 `.../data/`，剥离后才能落到同一 key |
| hybrid 合并是否仅限于跨类型 | 是 | 同类型同标题可能是不同文档（如两篇「Python 速览」），仅 personal_note × tech_doc 才判定为同一内容的两种载体 |
| hybrid 卡整体点击是否跳转 | 否 | 已有两个显式按钮，卡点击跳转易产生歧义（该跳本地还是网页？） |
| 章节列表是否完全移除 | 是 | V1.2 的章节列表与标题/摘要重复，且 V1.3 摘要已剥离标题行，章节信息冗余 |
| 本地路径如何展示 | 仅 basename | 完整路径既长又暴露目录结构，文件名已足够标识来源 |

---

## 附录 A：长期演进备忘（非 V1 范围，仅记录决策依据）

- **ChromaDB 恶化信号阈值**：chunk > 10 万、单次检索 > 1 秒、内存 > 可用 RAM 70%、索引文件 > 预期 10 倍、Recall@5 < 50% —— 触发任一即启动迁移评估
- **迁移路径**：ChromaDB → Qdrant（已有 VectorStore 抽象，只需新增 QdrantStore 实现）
- **分阶段选型**：< 5 万 chunk 用 ChromaDB + MySQL；5 万~50 万加量化压缩；> 50 万迁 Qdrant
- **评估指标目标**（未来版本）：引用命中率 ≥ 85%、忠实度 ≥ 90%、拒答准确率 ≥ 95%、首 Token 延迟 ≤ 1200ms

# PTDRA 知识库上传功能：项目解读、Yuxi 对标与实施方案

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.0（源码核验完成，待评审实施） |
| 编写日期 | 2026-10-23 |
| 适用项目 | Technical-Documentation-Assistant（PTDRA） |
| 对标项目 | D:/Downloads/Yuxi-main（Yuxi 智能知识库与知识图谱平台 v0.7.3） |
| 范围 | 仅新增“知识库上传（多格式文档解析 + 切片 + 向量索引）”能力；不改现有检索、问答、引用行为 |
| 状态 | 方案已对照两侧源码逐项核验；评审通过后按第 6 章分阶段实施 |

---

## 前置：本轮源码核验基线（2026-10-23）

> v0.1 草案中的推断已在本轮被实测/源码核验替代；以下结论直接决定实施方案，后文均已同步。

### A. 运行环境（实测）

| 项 | 实测结果 | 对方案的影响 |
| --- | --- | --- |
| 解释器 | 项目 `.venv` = **CPython 3.12.7**（base: `...\Programs\Python\Python312`） | v0.1 的“Python 3.14 无 onnxruntime 轮子”风险前提**不成立** |
| onnxruntime | **已安装 1.31.0** | RapidOCR 本地 OCR 可直接启用 |
| chromadb | **1.5.9**，rust 绑定在本机正常 | 上传功能**不要**降级 chromadb、不要改 Settings |
| 可安装解析依赖 | pypdf 6.21.2 / python-docx 1.2.0 / python-pptx 1.0.2 / openpyxl 3.1.5 / pandas 3.1.0 / xlrd 2.0.2 / markdownify 1.2.3 / rapidocr 3.10.0 / pypdfium2 5.14.0（`pip install --dry-run --only-binary :all:` 全部命中 cp312-win wheel） | P0/P1 无需编译器、无需额外环境 |
| 前端依赖 | marked 15 + DOMPurify 3.2 已在 `frontend/package.json` | 解析结果预览无需新增库 |

### B. Yuxi 源码核验（只列与本方案直接相关、且已逐文件确认的结论）

| 结论 | 证据文件 | 关键事实 |
| --- | --- | --- |
| 解析器统一接口 | `backend/package/yuxi/knowledge/parser/base.py` | `BaseDocumentProcessor`：`service_name / supported_extensions / process_file() / check_health()` |
| 能力注册表 | `parser/capabilities.py` | `PARSER_CAPABILITIES` 声明 7 个引擎的 `module_path/class_name/扩展名`；最常用的是 `rapid_ocr`、`mineru_ocr`、`pp_structure_v3_ocr` |
| 懒加载工厂 | `parser/factory.py` | `DocumentProcessorFactory.get_processor()` 按需 `import_module`，实例缓存键含初始化参数摘要 |
| MinerU | `parser/mineru.py` | `POST {MINERU_API_URI}/file_parse`；multipart `files` + 参数 `lang_list/backend/parse_method/formula_enable/table_enable/image_analysis/start_page_id/end_page_id/return_md/response_format_zip=true/return_images`；响应为 **ZIP**；健康检查 `GET /openapi.json` 且 paths 含 `/file_parse`；默认超时 1800s |
| MinerU ZIP 处理 | `parser/zip_utils.py` | 先校验绝对路径/`..`；优先取 `full.md`；`find_images_directory` 定位 `images/`；图片上传对象存储后 `replace_image_links` 改写 Markdown 图片链接 |
| RapidOCR | `parser/rapid_ocr.py` | rapidocr 3.x：`RapidOCR(params={...})`，ONNXRUNTIME + PP-OCRv5；PDF 用 **pypdfium2** 逐页 `render(scale=2)` 后 OCR；输出纯文本（无版面/表格/图片结构）；`check_health()` 只报组件状态、不加载模型 |
| PaddleX PP-StructureV3 | `parser/pp_structure_v3.py` | `POST {PADDLEX_URI}/layout-parsing`，JSON `{file: base64, fileType: 0(PDF)/1(图片), useTableRecognition, useFormulaRecognition}`；结果取 `result.layoutParsingResults[].markdown.text` 按页拼接；健康 `GET /health`；**Yuxi 未抽取该响应中的图片** |
| Office 解析 | `parser/unified.py` | `.docx/.pptx/.xlsx/.xls` 走 **Docling** 的 backend（重依赖）；仅 `.docx` 失败时回退 `python-docx`；`.xls` 用 pandas+xlrd；`RapidOCR/MinerU/PaddleX` 只负责 PDF/图片 |
| 统一格式路由 | `parser/unified.py::parse_resolved_document` | `.pdf`→OCR 引擎或 `pypdf`（`ocr_engine=disable`）；`.txt/.md` 直读；`.html`→markdownify；`.csv/.xls`→pandas→Markdown；`.json`→代码块；`.zip`→递归 |
| 跨引擎降级 | `parser/unified.py`、`parser/factory.py` | **不存在**：MinerU/PaddleX 失败直接抛错；唯一回退是 `.docx` 的 docling→python-docx；全仓库 parser 无重试/信号量 |
| 结构化中间表示 | `parser/base.py`、`unified.py` | **不存在**：所有解析器统一返回 Markdown `str`；不读 `content_list.json`/`middle.json`，无 bbox/页码结构 |
| PDF 预检 | `parser/unified.py` + `utils/pdf_utils.py` | 任何 PDF 先 `validate_pdf_page_tree_loadable`（pypdfium2）：加密→`encrypted_pdf`、空→`empty_pdf`、坏页树→`invalid_pdf_page_tree` |
| 切片 | `knowledge/chunking/ragflow_like/` | `dispatcher.chunk_markdown` 按 `chunk_preset_id` 分发 general/qa/book/laws/semantic/separator；general 默认 `chunk_token_num=512`、`overlapped_percent=0`，token 超 1.5 倍硬切 |
| 状态机与任务 | `knowledge/base.py`、`services/knowledge_task_service.py` | 文件状态 `uploaded → parsing → parsed → indexing → indexed`（异常 `error_parsing/error_indexing`）；任务三阶段循环、单文件 try/except 隔离、进度 5%→100% |
| 上传接口 | `server/routers/knowledge_router.py` | `POST /knowledge/files/upload`：扩展名白名单、100MB 上限、SHA-256 判重、落对象存储；`POST /databases/{kb}/documents` 入队，解析/索引可分开调用 |
| 数据模型 | `storage/postgres/models_knowledge.py` | `KnowledgeFile(status, markdown_file, chunk_count...)`、`KnowledgeChunk(chunk_id, chunk_index, content...)` |

### C. 对 v0.1 草案的修正点

1. **删除“Python 3.14”风险**：项目运行于 3.12.7，RapidOCR 依赖可直接安装。风险改为“服务器/CI 可能未安装 OCR 依赖或未下载模型”。
2. **RapidOCR 细节按真实 API 落地**：`RapidOCR(params=...)` + `pypdfium2` 渲染，而不是泛泛的“onnxruntime 调用”。
3. **PaddleX 明确不抽图**：首版只取 `markdown.text`；图片能力由 MinerU 路径提供。
4. **Office 解析不走 Docling**：PTDRA 首版用 python-docx/python-pptx/openpyxl（轻量、已验证可装）；Docling 仅作为未来可选高保真后端，避免引入重量级依赖。
5. **切片默认值对齐**：沿用现有 `chunk_markdown` 的 512/64 字符策略；上传文档的表格/图片块单独处理，不照搬 Yuxi 的 token 近似算法。
6. **不照抄 Yuxi 的两处缺口**：
   - Yuxi **没有跨引擎自动降级**（MinerU/PaddleX 失败即失败，唯一回退是 `.docx`）。PTDRA 方案中的 `auto` 降级链属于**新增设计**，需自行实现并测试，不能假设 Yuxi 已有。
   - Yuxi **没有结构化中间表示**（只有 Markdown 字符串）。若 PTDRA 需要页码/表格/图片溯源，必须在解析器内自行保留（本方案用 `<!-- page:N -->` + `assets/` 约定实现，不引入 bbox 级 IR）。
7. **CSV 解析不照抄**：Yuxi 的 `.csv` 实现是“每行生成一张重复表头的小表”，方案中改为**整表 Markdown 输出 + 超长按行切分并重复表头**。

---

## 0. 结论摘要

1. **PTDRA 当前只支持 Markdown 入库**：本地笔记扫描 `.md`、爬虫落盘 Markdown，再进入统一的 `ingest_pipeline` → `chunk_markdown` → ChromaDB + MySQL + BM25。项目里没有文件上传接口、没有 PDF/Office 解析、没有图片/表格抽取、没有逐文件解析状态。
2. **Yuxi 的核心可复用经验**：把所有格式先统一转换成 **Markdown 中间表示（IR）**，再用解析器适配层（RapidOCR / MinerU / PaddleX 等）和统一的切片器进入向量库；配合“文件级状态机 + 异步任务 + 失败重试 + 图片落盘改链”解决长耗时解析。
3. **推荐落地方式**：新增独立的上传管道，但**不重写现有入库逻辑**。上传文件 → 解析成 Markdown（图片/表格抽取为资源与结构化块）→ 复用现有 `ingest_files` 写入 Chroma/MySQL/BM25。存储上复用 `source_type=tech_doc` + `origin=upload`（与现有“网页写回复用 tech_doc + origin=web_search”的做法一致），**避免改 MySQL 枚举和 Chroma 集合**，把改动面压到最小。
4. **引擎策略**：内置适配器，运行时可插拔。默认用轻量原生解析器（pypdf / python-docx / python-pptx / openpyxl / pandas）；本地 OCR 首选 RapidOCR（ONNX，无需外部服务）；高精度版面/表格/图片用 MinerU 或 PaddleX 的 HTTP 服务（Docker 或远程），不可用时自动降级，不阻断上传。
5. **交付顺序**：P0 原生解析 + 上传 API/状态 + 最小 UI；P1 表格/图片感知切片 + RapidOCR + PDF 文本层自动判定；P2 MinerU/PaddleX 适配 + 图片预览；P3 加固、测试、文档与验收。完整方案约 14~21 人日，MVP 约 8~10 人日。

---

## 1. PTDRA 项目解读

### 1.1 项目定位

PTDRA（Personal Technical Documentation Research Assistant）是一个**单用户、本地优先**的技术文档问答助手：

- 数据来源：本地 Markdown 笔记 + Crawl4AI 抓取的技术文档网页；
- 核心能力：混合检索（BGE 向量 + BM25 + RRF + Rerank）、五维提问评分、多轮改写、置信度兜底、SSE 流式回答；
- 回答必须带 `[来源N]` 引用，点击可联动来源面板、打开本地文件或网页；
- 设计目标是“检索不到就说找不到”，不编造引用。

这决定了新功能不能引入多用户/权限/多知识库等重体系；它应当是**在现有单库入库管道旁边增加一个“文件上传前置转换器”**。

### 1.2 现有分层架构

~~~text
┌──────────────────────────────────────────────────────────────┐
│ 前端 React + Vite + TS                                        │
│ ChatContainer / MessageBubble / CitationPanel / SourceCard    │
│ SSE 七类事件：meta status sources content confirm error done  │
└───────────────────────────┬──────────────────────────────────┘
                            │ /api/chat/stream（SSE）
┌───────────────────────────▼──────────────────────────────────┐
│ FastAPI 路由层                                                │
│ chat.py · documents.py · files.py · health.py                 │
├──────────────────────────────────────────────────────────────┤
│ Agent 执行层（rag_service.chat_flow）                         │
│ 提问评分 → 改写 → 检索定级 → 混合检索 → 置信度 → 兜底 → 生成  │
├──────────────────────────────────────────────────────────────┤
│ 知识处理层                                                    │
│ chunker（标题感知 + 递归 512/64）                             │
│ ChromaDB（tech_docs / personal_notes）                        │
│ MySQL（documents/chunks/tags/document_tags/ingest_tasks）     │
│ BM25（jieba + rank-bm25，内存索引）                           │
├──────────────────────────────────────────────────────────────┤
│ 数据采集层                                                    │
│ note_scanner（.md 扫描） · crawler（Crawl4AI） · web_writeback │
└──────────────────────────────────────────────────────────────┘
~~~

### 1.3 现有入库链路（逐文件）

关键入口是 `backend/app/services/ingest_pipeline.py` 的 `ingest_files`（第 63 行）：

1. **采集**：`note_scanner.scan_markdown_files` 递归扫描 `.md`，解析 front-matter（title/tags/created_at/updated_at/source_url），输出 `{file_path, source_type, title, tags, ..., content}`；爬虫与网页写回复用同构对象。
2. **变更检测**：对 `content` 计算 SHA-256；查 MySQL `documents.file_path`，哈希相同且非 `force_reingest` 则跳过。
3. **删除旧数据**：按 `documents.id` 查旧 chunk 的 `chroma_id` → `vector_store.delete_by_ids`；再按 `source_type + file_path` 删除残留向量；删除 MySQL 文档（级联 chunks/document_tags）。
4. **切片**：`prepare_chunks` 组装文件级元数据（file_path/source_type/title/tags/source_url/origin/content_hash 等），调用 `chunk_markdown`（`backend/app/core/chunker.py` 第 62 行）：按 `#`~`####` 标题栈生成 `heading_path`，代码围栏内的 `#` 不误判；超长小节用 `RecursiveCharacterTextSplitter`（512/64，中文分隔符优先级）；每个 chunk 带 `heading_path / chunk_index / total_chunks`，随后补 `global_index` 并做 Chroma 标量清洗。
5. **向量化入库**：`ChromaStore.add_chunks` 按 `file_path#global_index#hash12` 生成稳定 ID，100 条/批 upsert；集合按 `source_type` 分流（`tech_doc → tech_docs`，`personal_note → personal_notes`）。
6. **元数据落库**：MySQL `documents`（哈希、chunk_count）、`chunks`（chroma_id/heading_path/token_count）、`tags/document_tags`。
7. **索引刷新**：`documents.py` 的 `_run_ingest` 在后台任务结束后调用 `bm25.load_from_chroma` 并重建 `retrieval_scorer`，保证新文档参与 BM25 与检索定级。

### 1.4 现有检索与引用链路

- `rag_service.chat_flow`：输入校验 → 五维评分（`question_scorer`）→ 低分先 `confirm` → 多轮改写 → 检索定级（`retrieval_scorer`）→ `HybridRetriever.retrieve_with_fallback`（L3 向量+BM25+RRF+Rerank / L2 / L1 / L0）→ 置信度与兜底 → `build_context` → LLM 流式 → SSE。
- 来源编号：送入 LLM 的片段按 1..N 编号；`build_sources` → `merge_chunks_to_sources`（`rag_service.py` 第 189 行）按 `(source_type, 归一化路径)` 合并同文档多片段，personal_note 与 tech_doc 同标题再合并为 hybrid 卡。
- 前端 `SourceCard` 根据 `source_type` / `source_url` 决定“打开网页”还是“打开本地 md”；`files.py` 只允许打开 `backend/data/{notes,tech_docs}` 下的 `.md`。

### 1.5 现状边界（与新需求的差距）

| 能力 | 现状 | 对新功能的影响 |
| --- | --- | --- |
| 文件来源 | 仅目录扫描 `.md` + 爬虫 Markdown | 需要新增 multipart 上传接口与原始文件存储 |
| 格式解析 | 无 PDF/Word/PPT/Excel/HTML/图片解析 | 需要新增解析器适配层 |
| OCR/版面 | 无 | 需要 RapidOCR / MinerU / PaddleX 适配 |
| 图片/表格 | `chunk_markdown` 只把 Markdown 文本当纯文本 | 需要抽取资源、保留表格结构、生成图/表 chunk |
| 入库状态 | 只有批次级 `ingest_tasks`（pending/processing/completed/failed） | 需要文件级状态（排队/解析/已解析/入库/失败）与重试 |
| 异步执行 | FastAPI `BackgroundTasks`（进程内线程池） | 长耗时 OCR 需要单独串行执行器 + 启动恢复 |
| 引用预览 | 本地 md 用系统默认程序打开；网页用 URL | 上传文档需要“解析后 Markdown/原文件/图片”预览 |
| 来源类型 | 代码中多处硬编码 `tech_doc / personal_note` | 推荐复用 `tech_doc + origin=upload`，避免改枚举/集合 |

### 1.6 关键代码地图

| 关注点 | 文件 | 关键位置 |
| --- | --- | --- |
| 入库主流程 | `backend/app/services/ingest_pipeline.py` | `prepare_chunks` 第 31 行、`ingest_files` 第 63 行 |
| Markdown 切片 | `backend/app/core/chunker.py` | `chunk_markdown` 第 62 行 |
| 向量存储 | `backend/app/services/vector_store.py` | `_COLLECTIONS` 第 16 行、`add_chunks` |
| MySQL 元数据 | `backend/app/services/metadata_store.py` | `documents/chunks/tags/ingest_tasks` 模型与 CRUD |
| BM25 | `backend/app/core/bm25_index.py` | `load_from_chroma` 第 26 行 |
| 入库 API | `backend/app/routers/documents.py` | `POST /api/documents/ingest` 第 44 行 |
| 来源合并 | `backend/app/services/rag_service.py` | `merge_chunks_to_sources` 第 189 行 |
| 本地文件打开 | `backend/app/routers/files.py` | 白名单 `data/notes`、`data/tech_docs` |
| 配置 | `backend/app/config.py` | 路径、分块、检索、Web 参数 |
| 前端来源卡 | `frontend/src/components/SourceCard.tsx` | `isWebSource` / `openLocal` |
| 前端主容器 | `frontend/src/components/ChatContainer.tsx` | header / 空状态 / 输入区 |

---

## 2. Yuxi 项目解读（对标实现）

### 2.1 项目定位与技术栈

Yuxi 是一个完整的“智能知识库 + 知识图谱 + Agent”平台，不是单用户小工具。其知识库模块的技术栈明显重于 PTDRA：

- 后端：FastAPI + LangGraph/LangChain；
- 元数据：PostgreSQL；向量：Milvus；对象存储：MinIO；任务：Redis + arq；图谱：Neo4j；
- 前端：Vue；部署：Docker Compose（api / worker / postgres / redis / minio / milvus / neo4j / mineru / paddlex 等）。

因此对 PTDRA 而言，Yuxi 的价值不是“照搬基础设施”，而是**解析与入库的架构模式**。

### 2.2 知识库模块结构

~~~text
backend/package/yuxi/knowledge/
├── base.py                 # KnowledgeBase 抽象：add_file_record / parse_file / index_file / 查询/删除
├── manager.py              # 多知识库管理、文件树、统计
├── factory.py              # 按 kb_type 创建实现
├── schemas.py              # API 模型
├── parser/                 # 文档解析适配层
│   ├── base.py             # BaseDocumentProcessor：process_file / check_health / supported_extensions
│   ├── capabilities.py     # 引擎能力注册表：rapid_ocr / mineru_ocr / mineru_official / pp_structure_v3 / paddleocr...
│   ├── factory.py          # 懒加载、实例缓存、按 engine_id 构造
│   ├── unified.py          # 按扩展名路由 + PDF/图片 OCR + Office/HTML/CSV/JSON/ZIP
│   ├── rapid_ocr.py        # 本地 ONNX OCR
│   ├── mineru.py           # MinerU HTTP /file_parse，ZIP 输出
│   ├── mineru_official.py  # MinerU 官方云 API
│   ├── pp_structure_v3.py  # PaddleX PP-StructureV3 /layout-parsing
│   ├── paddleocr_api.py    # PaddleOCR-VL / PP-OCRv6 云 API
│   ├── deepseek_ocr.py     # DeepSeek-OCR 云 API
│   └── zip_utils.py        # ZIP 内 full.md + images，图片上传 MinIO、改链
├── chunking/ragflow_like/
│   ├── dispatcher.py       # chunk_preset_id → general/qa/book/laws/semantic/separator
│   ├── nlp.py              # token 近似计数、naive_merge、层级合并、硬切
│   ├── presets.py          # 预设与参数合并
│   └── parsers/            # general/qa/book/laws/semantic/separator
├── implementations/
│   ├── milvus.py           # Milvus 实现：切片、embedding、写 PG + Milvus、检索
│   └── dify.py / notion.py # 外部知识库
├── graphs/                 # 图谱抽取/向量
└── utils/                  # kb_utils、pdf_utils、security、url_fetcher...
~~~

### 2.3 上传 → 解析 → 切片 → 索引全链路

#### 第 1 步：上传文件（`knowledge_router.py` 的 `/files/upload`，约 715~829 行）

- `POST /api/knowledge/files/upload`：multipart 单文件；
- 扩展名白名单 `is_supported_file_extension`，单文件上限 100 MB（`upload_utils.MAX_UPLOAD_SIZE_BYTES`，**分块累计校验**，不是读完再判断）；
- 读取时计算 **SHA-256** 并返回 `file_path / content_hash / filename / size / same_name_files`；
- **只把原文件写入对象存储，不创建数据库记录**；对象键 `{kb_id}/upload/{basename}_{ms}{ext}`（`documents` 与 `parsed` 共用 `knowledgebases` 桶，仅前缀不同；图片在私有桶 `kb-images`）；
- 真正的判重发生在“加记录/入库”阶段：同 KB 内 `content_hash` 已存在 → 409；但**同一上传批次内的重复内容不会被去重**（上传时库里还没有记录，且无唯一约束）——PTDRA 需在批量接口里用内存 Set 补上。

#### 第 2 步：添加文件记录与入队（两段式）

- `POST /databases/{kb_id}/documents/add`（第 761 行）：把已上传对象登记为 `KnowledgeFile`，**只写元数据，不解析**，状态 `uploaded`。
- `POST /databases/{kb_id}/documents`（第 716 行）：把 items + params 封装成 `knowledge_ingest` 任务，交给 `tasker.enqueue`；返回 `task_id`，前端在任务中心看进度。
- 也可分别调用 `/documents/parse`（第 922 行）、`/documents/index`（第 967 行），实现“先解析、人工确认后再入库”。
- `KnowledgeFile.kb_id` 是**单值外键**：一个文件只属于一个知识库；Milvus 也按 `kb_id` 一库一 collection，**没有跨库联合检索**。

#### 第 3 步：异步任务编排（`knowledge_task_service.run_knowledge_ingest` 第 35 行）

- 阶段 1/3：逐个 `add_file_record`；
- 阶段 2/3：逐个 `parse_file`；
- 阶段 3/3：`auto_index=true` 时逐个 `update_file_params` + `index_file`；
- 每个文件独立 try/except，单文件失败不影响整批；最终汇总 `submitted / failed / items`；
- 任务带 `processing_task_id + processing_owner`，用于并发占位、取消与失败收敛。

#### 第 4 步：解析文件（`knowledge/base.py` 的 `parse_file` 第 240 行）

- 状态机原子占位：`uploaded → parsing`（`update_fields_if_status`），并发/重复解析会被拒绝；
- 从 KB 与文件参数合并出 `processing_params`；设置图片 bucket/prefix；
- 调用 `yuxi.services.ocr_service.parse_document`（第 75 行）；
- 解析结果 Markdown 存 MinIO（`markdown_file`），状态 `parsing → parsed`；
- 异常 → `error_parsing`，记录 `error_message`，清理 processing_owner。

#### 第 5 步：统一解析入口（`ocr_service.parse_document` + `parser/unified.py`）

- 对 PDF/图片：先解析 OCR 引擎（DB 配置 → 环境变量 → Provider Key），再校验引擎是否支持该扩展名；
- 对文本/Office/表格/HTML/CSV/JSON/ZIP：参数原样传入统一解析器；
- 唯一业务入口是 `parse_document`，底层 parser 不查数据库、不关心配置来源。

`unified.parse_resolved_document`（第 116 行）按扩展名路由：

| 扩展名 | 处理方式 | 产出 |
| --- | --- | --- |
| `.pdf` | 先 `validate_pdf_page_tree_loadable`；`ocr_engine=disable` 时 `pypdf` 提取文本，否则交给所选 OCR 引擎 | Markdown/文本 |
| `.txt/.md` | aiofiles 直接读 | 原文 |
| `.docx` | Docling Slim `MsWordDocumentBackend`；失败回退 `python-docx`（段落 + 表格） | Markdown |
| `.pptx` | Docling Slim `MsPowerpointDocumentBackend` | Markdown |
| `.xlsx` | Docling Slim `MsExcelDocumentBackend` | Markdown |
| `.xls` | pandas + xlrd，逐 sheet 转 Markdown | Markdown |
| `.csv` | pandas 逐行转 Markdown | Markdown |
| `.html/.htm` | markdownify（ATX 标题） | Markdown |
| `.json` | `json.dumps(indent=2)` 包进代码块 | Markdown |
| 图片 | 必须启用 OCR，否则报错 | 文本 |
| `.zip` | 递归处理（安全路径校验） | Markdown |

注意：Yuxi **不支持 legacy `.doc/.ppt`**（能力表只有 `.docx/.pptx`），也不支持 `.docm/.xlsm` 等宏格式。

#### 第 6 步：切片（`chunking/ragflow_like`）

- `dispatcher.chunk_markdown`（第 70 行）根据 `chunk_preset_id` 分发：
  - `general`（默认）：按分隔符/换行切段，`naive_merge` 合并到 token 上限；
  - `qa`：优先识别问答结构（上限 4000 字符）；
  - `book`：章节层级合并（depth=5）；
  - `laws`：法条层级（depth=3）；
  - `semantic`：嵌入 + 聚类语义切分（需要 embedding）；
  - `separator`：命中分隔符即切。
- `nlp.count_tokens`（第 49 行）是**近似 token**（英文词 + 数字 + CJK 单字），默认 `chunk_token_num=512`、`overlapped_percent=0`，general 允许 1.5 倍硬上限再切；**没有最小 chunk size**。
- 每个 chunk 记录 `chunk_id / chunk_index / content / start_char_pos / end_char_pos`；`chunk_id = {file_id}_chunk_{idx}`。
- **溯源缺口（已核验）**：`start_token_pos/end_token_pos` 恒为 `None`；没有页码、标题路径、图片 URL、表格类型等字段；PDF 解析把各页文本直接 `\n\n` 拼接，页号丢失。PTDRA 若需要页码，必须在解析阶段自行写入 Markdown 标记。
- 未发现 parent-child（父子块）分块；6 种 preset 都是单层切分。

#### 第 7 步：索引（`implementations/milvus.py` 的 `index_file` 第 678 行）

- 状态 `parsed → indexing`（原子占位）；
- 读 MinIO 中的 Markdown → `chunk_markdown`；
- 先删旧 chunk（重入幂等）→ 分批 embedding（`MILVUS_CHUNK_EMBED_BATCH_SIZE=200`）→ **先写 PostgreSQL `knowledge_chunks`（权威事实），再 upsert Milvus**（派生索引，尝试 3 次、间隔 1s）；
- **PG 与 Milvus 是弱一致**：Milvus 失败不回滚 PG，存在“PG 有 chunk、向量库缺失”的窗口，只能靠重新入库收敛；
- 更新 `chunk_count / token_count`，状态 `indexing → indexed`；
- 异常 → `error_indexing`，记录错误，可重试；已解析文件不能直接重解析（只允许 `uploaded/error_parsing/failed` 进入解析）。

### 2.4 解析器抽象层（最值得借鉴的部分）

~~~python
class BaseDocumentProcessor(ABC):
    service_name: ClassVar[str]
    display_name: ClassVar[str]
    supported_extensions: ClassVar[list[str]]

    @abstractmethod
    def process_file(self, file_path: str, params: dict | None = None) -> str: ...

    @abstractmethod
    def check_health(self) -> dict: ...
~~~

- `capabilities.py` 用 `ParserCapability` 数据类集中声明每个引擎的 **id / 展示名 / 支持扩展名 / 模块路径 / 类名**；
- `factory.py` 根据 id **懒加载模块**（避免启动时 import torch/onnxruntime），按初始化参数哈希缓存实例，支持健康检查与缓存清理；
- 业务层只依赖 `process_file` 和 `check_health`，不关心引擎是本地、HTTP 还是云 API。
- **重要事实（已核验）**：Yuxi 的工厂与路由**不做跨引擎降级**。`parse_pdf`/`parse_image` 捕获异常后直接抛出；唯一回退是 `.docx` 的 docling → python-docx。PTDRA 若要 `auto` 降级链，属于新增设计（见 5.4），不能声称“复刻 Yuxi”。
- 所有 PDF 在选引擎前先做页树预检（`validate_pdf_page_tree_loadable`，pypdfium2）：加密/空/坏页树直接以 `encrypted_pdf/empty_pdf/invalid_pdf_page_tree` 失败。
- 解析器与对象存储强耦合（MinerU/PaddleOCR-VL/docling 内部直传 MinIO）。PTDRA 版本改为“解析器只落盘资源 + 返回相对路径”，存储与 URL 由服务层统一处理，便于单测。

这个模式对 PTDRA 非常合适：解析器重依赖多、启动慢，必须懒加载；引擎不可用时也不能拖垮主服务。

### 2.5 OCR/解析引擎对比（Yuxi 已实现）

| 引擎 | 输入 | 产出 | 部署 | 优点 | 局限 |
| --- | --- | --- | --- | --- | --- |
| RapidOCR（`rapid_ocr`） | PDF/图片 | 纯文本（`result.txts` 按页拼接） | 本地 ONNX（PP-OCRv5），pip 安装 | 无需外部服务、CPU 可跑、快 | **不保留版面/表格/图片**，扫描件只有文字行 |
| MinerU（`mineru_ocr`） | PDF/图片 | ZIP：`full.md` + `images/` | 独立 HTTP 服务（Docker/GPU） | 版面、表格、公式、图片质量高 | 服务重、首次模型下载慢、同步长请求、许可证需注意 |
| MinerU Official | PDF/图片（能力表声明 DOCX/PPTX，但路由实际不走） | Markdown/结果文件 | 云 API（mineru.net 三阶段） | 免部署、质量高 | 文件出境、需 Key、按量计费；`model_version` 未真正发送 |
| PP-StructureV3（PaddleX） | PDF/图片 | 每页 `markdown.text`（含表格/公式） | PaddleX serving（Docker/GPU） | 中文版面/表格强、可本地 | 部署重、GPU 推荐、**不迁移图片** |
| PaddleOCR-VL / PP-OCRv6 | PDF/图片 | Markdown（含图片转存）/纯文本 | 云 API | 免部署 | 文件出境、需 Key |
| DeepSeek-OCR | PDF/图片 | 文本/Markdown | 云 API（SiliconFlow） | 免部署 | 文件出境、需 Key |

补充核验：Yuxi 默认 OCR 引擎为 `rapid_ocr`；`disable` 在 API 层可用（PDF 退化为 pypdf 文本层），但前端 `OCRSelector` 从未传 `include-disable`，所以 UI 上无法选择 `disable`。

对 PTDRA 的建议：**RapidOCR 本地兜底 + MinerU/PaddleX 可选高精度 + 云 API 仅显式开启**。不要一开始就接 7 个引擎。

### 2.6 图片与表格处理

- **图片**：MinerU ZIP 内 `images/` → 对象存储 → `replace_image_links` 改写 Markdown 链接；Docling 解析 Office 时把 `data:image/...` 上传并替换 `<!-- image -->` 占位。**PaddleX 不迁移图片**（只取 `markdown.text`），RapidOCR 无图片。
- **表格**：MinerU/PaddleX 直接输出 Markdown 表格；Docling 输出 Office 表格；`python-docx` 兜底时手工拼 Markdown 表格。
- **RapidOCR 路径不抽图/表**，只输出文本；这是“快而糙”的取舍。
- **不要照抄 Yuxi 的图片前缀缺陷**：Yuxi 多处默认 `unknown/kb-images`，会产生指向不存在知识库的图片 URL。PTDRA 直接用 `{upload_id}/assets/` 本地路径，天然无此问题。

### 2.7 任务、状态机与可靠性

- 文件状态：`uploaded → parsing → parsed → indexing → indexed`；异常分支 `error_parsing / error_indexing`；解析只允许从 `uploaded/error_parsing/failed` 进入，入库允许从 `parsed/error_indexing/indexed` 重入。
- 任务状态：数据库里只有 `pending / running / success / failed / cancelled`；前端看到的 `queued` 只是 `pending` 的展示别名（易误读点）。
- 进度：批处理阶段 **5% → 30% → 60% → 95% → 100%**，按“已完成文件数 / 总数”推进；进度落库有 2% 节流。
- 并发控制：`DURABLE_TASK_MAX_RUNNING=4`、任务默认超时 6h；`processing_task_id + processing_owner` 原子占位；任务可取消。
- 持久化：任务 payload 存 PG，worker 重启后可按 payload 恢复；Redis/ARQ 只负责调度。
- 单文件失败隔离：批量任务中一个文件失败不会阻断其余文件。
- 前端：任务进度**轮询** `/api/tasks`，没有通用 SSE（只有虚拟文件夹迁移有单任务 SSE）；上传用 XHR 队列（并发 10）以获取逐文件进度。

### 2.8 可借鉴 / 应简化 / 不要照搬

| 类别 | 内容 |
| --- | --- |
| **直接借鉴** | Markdown 作为统一中间表示；解析器能力注册 + 工厂 + 懒加载；文件级状态机；单文件失败隔离；图片落盘/改链；ZIP 安全解压；引擎健康检查；切片预设与 token 上限保护 |
| **按 PTDRA 简化** | 不引入 MinIO：本地 `backend/data/uploads`；不引入 Redis/arq：进程内单线程执行器 + MySQL 状态表；不引入 Milvus/PG：复用 Chroma + MySQL；不做多知识库/权限/图谱/评估 |
| **不要照搬** | 7 个 OCR 引擎全接；Docling 作为硬依赖（重）；Milvus + PG 双写复杂度；Vue 前端；多租户资源模型 |
| **要改进** | ① Yuxi 对 PDF 默认直接 OCR（除非 disable）；PTDRA 先检测文本层，数字 PDF 走原生文本，扫描件才 OCR；② 上传/建记录两段式 → PTDRA 一次请求内完成，避免孤儿对象；③ 批次内容哈希去重（Yuxi 只查库，批内重复漏网）；④ PDF 页码标记（Yuxi 页号丢失）；⑤ 表格/图片 chunk 语义（Yuxi chunk 无类型/页码/图片字段）；⑥ 入库失败清理残留向量（Yuxi PG/Milvus 弱一致、失败不回滚） |

---

## 3. 需求拆解与关键决策

### 3.1 需求拆解

| 编号 | 需求 | 可验收的落地物 |
| --- | --- | --- |
| R1.1 | 支持 PDF、Word、PPT、Excel、Markdown 等常见格式 | 扩展名路由 + 原生解析器 + 清晰的不支持提示 |
| R1.2 | 内置 MinerU、PaddleX、RapidOCR 等解析引擎 | 统一 `DocumentParser` 接口 + 三个适配器 + 健康检查 + 自动降级 |
| R1.3 | 提取文本、图片、表格内容 | Markdown IR + `assets/` 图片 + Markdown 表格 + 图/表 chunk |
| R1.4 | 切分为 Chunk，生成向量索引供检索 | 复用 `ingest_files` → Chroma/MySQL/BM25，新增图/表感知切片 |
| R1.5 | 不改变现有功能 | 现有测试全绿；旧来源类型/引用/检索行为不变；新增能力靠可选参数/新路由 |

### 3.2 约束

- **单用户、本地优先**：不引入账号、权限、多知识库隔离。
- **Windows + 现有 Python 环境**：实测 `.venv` 为 CPython 3.12.7（base Python312），onnxruntime 1.31 已安装；pypdf/python-docx/python-pptx/openpyxl/rapidocr 等均有 cp312-win wheel。仍保留“引擎不可用”降级，避免把 OCR 变成启动硬依赖（服务器/CI 可能未装模型）。
- **现有 MySQL/Chroma 数据不能重建**：新增能力必须向后兼容。
- **长耗时任务不能阻塞 SSE/对话**：解析放在独立执行器。
- **无 Docker 也必须可用**：原生解析 + 可选本地 OCR 是基线；MinerU/PaddleX 是可选增强。

### 3.3 关键决策

| 决策 | 推荐 | 理由 | 备选 |
| --- | --- | --- | --- |
| D1 来源类型 | **复用 `source_type=tech_doc` + `origin=upload`** | 不改 MySQL 枚举、不改 Chroma 集合、BM25/检索自动覆盖；与现有 web_writeback 的 `tech_doc + origin=web_search` 先例一致 | 新增 `uploaded_doc` 类型/集合，隔离更干净但需改枚举、集合映射、BM25、迁移 |
| D2 解析架构 | 能力注册表 + 工厂 + 懒加载 | 重依赖不拖慢启动；引擎可插拔；健康检查统一 | 在 service 里写 if/else（难维护） |
| D3 引擎策略 | 原生优先；PDF 先文本层；扫描件 RapidOCR/MinerU/PaddleX；云 API 仅显式开启 | 成本/隐私/可用性平衡 | 全部走 MinerU（重）；全部走云（隐私/费用） |
| D4 切片 | 现有 `chunk_markdown` 保持不变；上传路径用新增 `chunk_uploaded_document` | 表格/图片需要特殊处理，但不能改变旧文档切片行为 | 直接改 `chunker.py`（回归风险大） |
| D5 异步 | 进程内 `ThreadPoolExecutor(max_workers=1)` + MySQL 状态表 + 启动恢复 | 个人工具无需 Redis/arq；串行避免 OCR 并发打爆内存 | Celery/arq（过重） |
| D6 资源存储 | 本地 `backend/data/uploads/<upload_id>/` | 无 MinIO 依赖；便于预览/清理 | MinIO（TDA 无此基础设施） |
| D7 前端入口 | header “知识库”抽屉 + 上传/文档/引擎三个页签 | 不打断对话主界面；符合现有玻璃风格 | 独立页面（导航改动大） |

---

## 4. 目标架构

### 4.1 数据流

~~~text
用户选择文件
   │
   ▼
POST /api/uploads（multipart，多文件）
   │ 扩展名/大小/数量校验；SHA-256 判重；落盘
   ▼
upload_tasks + upload_files（MySQL，状态 queued）
   │  提交到单线程执行器
   ▼
[1] 解析（parser registry）
   ├─ native：pypdf / python-docx / python-pptx / openpyxl / pandas / markdownify
   ├─ rapidocr：pypdfium2 渲染 + RapidOCR（本地 ONNX）
   ├─ mineru：HTTP /file_parse → ZIP(full.md + images)
   └─ paddlex：HTTP /layout-parsing → 每页 Markdown
   │
   ▼
ParsedDocument { markdown, assets[], page_count, warnings }
   │  Markdown 落盘 parsed.md；图片落盘 assets/
   ▼
[2] 切片（chunk_uploaded_document）
   ├─ 标题感知文本块 → RecursiveCharacterTextSplitter（512/64）
   ├─ 表格块 → Markdown 表格；超长按行切且重复表头
   └─ 图片块 → figure chunk（caption/OCR/文件名 + asset 元数据）
   │
   ▼
[3] 复用 ingest_files（chunker 参数注入）
   ├─ ChromaDB tech_docs（bge 向量，100/批）
   ├─ MySQL documents/chunks/tags
   └─ BM25 load_from_chroma + retrieval_scorer 重建
   │
   ▼
状态 indexed；前端可检索/引用/预览
~~~

### 4.2 新增/修改模块

~~~text
backend/app/
├── parsers/                     # 新增：解析器适配层
│   ├── __init__.py
│   ├── base.py                  # DocumentParser / ParsedDocument / ParsedAsset
│   ├── registry.py              # 能力表 + 工厂 + 健康缓存
│   ├── native.py                # md/txt/html/pdf/docx/pptx/xlsx/xls/csv/json
│   ├── rapidocr_engine.py       # 本地 OCR
│   ├── mineru_engine.py         # MinerU HTTP
│   ├── paddlex_engine.py        # PaddleX PP-StructureV3 HTTP
│   └── zip_safe.py              # 安全解压与图片收集
├── core/
│   └── rich_chunker.py          # 新增：表格/图片感知切片
├── services/
│   ├── upload_store.py          # 新增：upload_tasks/upload_files/upload_assets CRUD
│   ├── upload_pipeline.py       # 新增：校验→解析→切片→入库→状态更新
│   └── asset_store.py           # 新增：图片落盘、URL 生成、清理
├── routers/
│   └── uploads.py               # 新增：/api/uploads/*
├── models/
│   └── schemas.py               # 修改：SourceItem 可选 upload_id/doc_format
├── config.py                    # 修改：upload_* / engine_* 配置
├── state.py                     # 修改：UploadStore/执行器/refresh 助手
├── main.py                      # 修改：注册 uploads 路由、关闭执行器
└── services/
    ├── ingest_pipeline.py       # 修改：可选 chunker / extra_metadata
    ├── metadata_store.py        # 修改：新增 ORM 模型
    └── rag_service.py           # 修改：hybrid 合并排除 origin=upload

frontend/src/
├── components/
│   ├── KnowledgeBasePanel.tsx   # 新增：抽屉
│   ├── UploadDropzone.tsx       # 新增：拖拽/选择
│   ├── UploadList.tsx           # 新增：文件状态/操作
│   ├── UploadPreviewDialog.tsx  # 新增：Markdown/图片预览
│   └── SourceCard.tsx           # 修改：uploaded 分支
├── hooks/useUploads.ts          # 新增：上传/轮询/重试
├── api/client.ts                # 修改：upload API
└── types/chat.ts / upload.ts    # 修改/新增
~~~

---

## 5. 详细设计

### 5.1 数据模型

#### 5.1.1 来源类型策略（推荐方案 A）

上传文档在 MySQL 中仍写 `source_type="tech_doc"`，在 chunk 元数据中写：

~~~json
{
  "source_type": "tech_doc",
  "origin": "upload",
  "upload_id": "u_20261023_ab12cd34",
  "doc_format": "pdf",
  "parse_engine": "mineru",
  "original_filename": "FastAPI 官方文档.pdf",
  "file_path": "data/uploads/u_20261023_ab12cd34/FastAPI 官方文档.pdf"
}
~~~

这样：

- `ChromaStore._COLLECTIONS` 不需要新增映射；
- `BM25Index.load_from_chroma` 不需要改循环；
- MySQL `documents.source_type` 枚举不需要 ALTER；
- 现有 `source_type` 语义仍是“文档大类”，`origin` 表示“来源方式”（与 web_search 一致）。

**唯一需要补的现有逻辑**：`merge_chunks_to_sources` 的 personal_note × tech_doc 同标题 hybrid 合并，必须排除 `origin=upload`，否则上传的 PDF 可能被误合并成“本地笔记 + 网络文章”卡片。改动一行守卫即可，旧数据行为不变。

#### 5.1.2 新增表（DDL）

~~~sql
-- 上传批次
CREATE TABLE IF NOT EXISTS upload_tasks (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id         VARCHAR(64) NOT NULL UNIQUE,
    status          VARCHAR(32) NOT NULL DEFAULT 'queued', -- queued/running/completed/partial/failed/cancelled
    engine          VARCHAR(32) NOT NULL DEFAULT 'auto',
    total_files     INT NOT NULL DEFAULT 0,
    processed_files INT NOT NULL DEFAULT 0,
    failed_files    INT NOT NULL DEFAULT 0,
    error_message   TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at    DATETIME,
    INDEX idx_upload_task_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 单文件记录（前端列表/状态/重试的依据）
CREATE TABLE IF NOT EXISTS upload_files (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    upload_id         VARCHAR(64) NOT NULL UNIQUE,
    task_id           VARCHAR(64),
    original_filename VARCHAR(512) NOT NULL,
    stored_path       VARCHAR(1024) NOT NULL,  -- data/uploads/<id>/<safe_name>
    markdown_path     VARCHAR(1024),           -- data/uploads/<id>/parsed.md
    doc_format        VARCHAR(16) NOT NULL,
    file_size         BIGINT NOT NULL,
    content_hash      CHAR(64) NOT NULL,          -- 原文件 SHA-256（用于上传去重），与 documents.content_hash（Markdown 哈希）不同
    status            VARCHAR(32) NOT NULL DEFAULT 'queued',
    auto_index        TINYINT(1) NOT NULL DEFAULT 1,  -- 解析完成后是否自动入库
    parse_engine      VARCHAR(32),
    chunk_size        INT NOT NULL DEFAULT 512,
    chunk_overlap     INT NOT NULL DEFAULT 64,
    progress          INT NOT NULL DEFAULT 0,
    error_message     TEXT,
    document_id       BIGINT,
    chunk_count       INT NOT NULL DEFAULT 0,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_upload_file_status (status),
    INDEX idx_upload_file_task (task_id),
    INDEX idx_upload_file_hash (content_hash),
    CONSTRAINT fk_upload_file_document FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 图片/表格资源（可选，P2 启用）
CREATE TABLE IF NOT EXISTS upload_assets (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    upload_id   VARCHAR(64) NOT NULL,
    asset_type  VARCHAR(16) NOT NULL, -- image/table
    name        VARCHAR(512) NOT NULL,
    rel_path    VARCHAR(1024) NOT NULL,
    page_no     INT,
    caption     TEXT,
    ocr_text    MEDIUMTEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_upload_asset_upload (upload_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
~~~

ORM 模型建议直接定义在 `backend/app/services/metadata_store.py` 中（复用同一个 `Base`，保证 `Base.metadata.create_all` 能建表），CRUD 放在 `upload_store.py`。这样不需要额外迁移工具；新表由 `create_all` 自动创建。

#### 5.1.3 备选方案 B：强隔离（不推荐用于首版）

如果要让上传文档在 Chroma 中独立集合：

- `documents.source_type` 从 ENUM 扩为 `VARCHAR(32)`（或加枚举值），执行一次 `ALTER TABLE`；
- `_COLLECTIONS` 增加 `uploaded_doc → uploaded_docs`；
- BM25 改为遍历 `_COLLECTIONS`；
- `SourceCard`/引用标签增加类型；
- 旧数据不变，但改动面明显更大。

结论：首版用方案 A；若后续上传文档量大到需要独立重建/清理，再迁移到方案 B。

### 5.2 本地存储布局

~~~text
backend/data/uploads/
└── u_20261023_ab12cd34/
    ├── original.pdf          # 实际落盘文件（安全化后的原文件名）
    ├── parsed.md             # 解析后的 Markdown（图片相对链接 assets/...）
    └── assets/
        ├── image_001.png
        └── table_001.md      # 可选：表格单独留档
~~~

约定：

- 目录名使用 `upload_id`（uuid/时间戳+随机），**永不用用户文件名拼路径**；
- 落盘文件名做 Windows 非法字符替换、长度截断（保留扩展名）；
- 所有路径解析后必须 `is_relative_to(uploads_root)`；
- 删除上传时整目录删除；失败重试复用同一 `upload_id`（先清理 `parsed.md/assets`）。

### 5.3 解析器抽象

~~~python
# backend/app/parsers/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class ParsedAsset:
    kind: str                  # "image" | "table"
    name: str                  # "image_001.png"
    rel_path: str              # "assets/image_001.png"
    page_no: int | None = None
    caption: str = ""
    ocr_text: str = ""
    width: int | None = None
    height: int | None = None

@dataclass
class ParsedDocument:
    markdown: str
    assets: list[ParsedAsset] = field(default_factory=list)
    engine: str = "native"
    page_count: int | None = None
    warnings: list[str] = field(default_factory=list)

class DocumentParser(ABC):
    name: str
    supported_extensions: tuple[str, ...]

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def health(self) -> dict: ...

    @abstractmethod
    def parse(self, src: Path, out_dir: Path, params: dict) -> ParsedDocument: ...
~~~

能力注册表：

~~~python
# backend/app/parsers/registry.py
PARSERS = {
    "native": NativeParser,
    "rapidocr": RapidOCRParser,
    "mineru": MinerUParser,
    "paddlex": PaddleXParser,
}

def get_parser(engine_id: str) -> DocumentParser:
    # 懒加载 + 实例缓存（同 Yuxi DocumentProcessorFactory）
    ...

def engine_capabilities() -> list[dict]:
    # 返回 id/name/available/reason/formats/mode，供 /api/uploads/engines
    ...
~~~

要点：

- 模块顶层**不要 import** rapidocr/onnxruntime；在 `available()/parse()` 内延迟导入；
- 健康检查缓存 30s，超时 2s；
- 引擎初始化失败只影响该引擎，不影响主服务启动；
- 解析器只接收“本地文件路径 + 输出目录 + 已解析参数”，不查 DB。

### 5.4 自动引擎路由

~~~python
NATIVE_FORMATS = {"md", "markdown", "txt", "html", "htm",
                  "docx", "pptx", "xlsx", "xls", "csv", "json"}
IMAGE_FORMATS = {"png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"}
PDF_FORMATS = {"pdf"}

def choose_engine(path: Path, requested: str) -> str:
    fmt = path.suffix.lower().lstrip(".")
    if requested != "auto":
        return requested
    if fmt in NATIVE_FORMATS:
        return "native"
    if fmt in PDF_FORMATS:
        # 前 20 页抽样：平均字符数 >= 80 视为数字 PDF
        if pdf_text_chars_per_page(path, sample=20) >= 80:
            return "native"
        return first_available(["mineru", "paddlex", "rapidocr"]) or "native"
    if fmt in IMAGE_FORMATS:
        return first_available(["rapidocr", "paddlex", "mineru"]) or "rapidocr"
    raise UnsupportedFormat(fmt)
~~~

解析失败自动降级（**PTDRA 新增设计**；Yuxi 本身没有此链条，见 2.4）：

~~~python
FALLBACK_ORDER = {
    "pdf":   ["mineru", "paddlex", "rapidocr", "native"],
    "image": ["rapidocr", "paddlex", "mineru"],
}

def parse_with_fallback(path: Path, out_dir: Path, params: dict, requested: str) -> ParsedDocument:
    primary = choose_engine(path, requested)
    fmt = path.suffix.lower().lstrip(".")
    bucket = "pdf" if fmt in PDF_FORMATS else "image"
    chain = [primary] + [e for e in FALLBACK_ORDER[bucket] if e != primary]
    errors = []
    for engine_id in chain:
        if engine_id == "native" and bucket == "image":
            continue
        parser = get_parser(engine_id)
        if not parser.available():
            errors.append(f"{engine_id}: 不可用")
            continue
        try:
            parsed = parser.parse(path, out_dir, params)
            if len(parsed.markdown.strip()) < 50:
                # 扫描件走到 pypdf 时常见：不能把“空解析”当成功
                errors.append(f"{engine_id}: 输出为空")
                continue
            return parsed
        except Exception as exc:
            errors.append(f"{engine_id}: {exc}")
    raise AllEnginesFailed(errors)
~~~

实现约束：

- `auto` 只允许选**本地/自托管**引擎；云 API 永不自动选。
- 降级顺序固定为 `mineru → paddlex → rapidocr → native(pypdf)`（PDF/图片），每次失败原因写进 `upload_files.error_message`（保留全部尝试记录）。
- `requests` 的 `timeout` 是连接/读取超时，不是总墙钟；worker 用 `concurrent.futures` 的 `future.result(timeout=upload_parse_timeout_seconds)` 做**单文件总时限**，超时即标记失败（线程可能仍跑到自身超时，但不会阻塞队列）。
- 解析前先做 PDF 页树预检（pypdfium2 或 pypdf）：加密→明确报错、0 页→明确报错；预检失败不进入降级链。

PDF 判定细节：

- 用 `pypdf.PdfReader` 抽样前 20 页，`sum(len(page.extract_text() or "")) / n`；
- 加密/损坏 PDF → 明确报错（`PdfReadError`）；
- 文本层足够 → 原生解析；否则进入 OCR 链；
- 混合型 PDF（部分扫描页）首版可整篇 OCR（质量一致）；P2 再做逐页混合。

### 5.5 各引擎适配器

#### 5.5.1 NativeParser（默认，无外部服务）

| 格式 | 实现 | 输出约定 | 限制 |
| --- | --- | --- | --- |
| `.md/.markdown/.txt` | 读取（utf-8，回退 gbk） | 原文；md 解析 front-matter | — |
| `.html/.htm` | BeautifulSoup 去 script/style/nav/footer/aside + markdownify | ATX Markdown | 复杂 JS 页面可能缺失内容 |
| `.pdf` | pypdf 逐页 `extract_text` | 每页前插入 `## 第 N 页` | 无版面/表格/图片结构；扫描件走 OCR；原生路径不抽图 |
| `.docx` | python-docx：正文段落 + 表格；标题样式映射 `#`；关系部件抽图片 | 标题 + 段落 + Markdown 表格 + 图片链接 | 复杂文本框/浮动图顺序可能不完美 |
| `.pptx` | python-pptx：逐 slide，标题作 `##`；文本框、表格、图片按 shape 顺序 | 每页标题 + 文本 + 表格 + 图片 | 组合形状/动画内容可能遗漏 |
| `.xlsx` | openpyxl（read_only, data_only）逐 sheet；`ws._images` 尽力抽内嵌图 | `## {sheet}` + Markdown 表格 + 图片链接 | 公式取缓存值；合并单元格取左上值；图片 API 非公开 |
| `.xls` | pandas + xlrd，`header=None` | 每个 sheet 一个 Markdown 表格 | 老格式依赖 xlrd |
| `.csv` | csv 模块（编码探测） | Markdown 表格 | 大文件限行 |
| `.json` | json.loads + 格式化 | json 代码块 | — |
| `.doc/.ppt` | 不在 MVP；可选 LibreOffice headless 转 docx/pptx | — | 需安装 LibreOffice |

实现要点：

- DOCX/PPTX/XLSX 的图片保存到 `out_dir/assets/`，Markdown 写相对链接 `assets/image_001.png`；PDF 原生解析**不抽图**，需要图片时选 MinerU/PaddleX；
- 表格统一走 `rows_to_markdown(rows)`：转义 `|`、去空行、补齐列、限制单元格长度；
- 所有文本块之间保留空行，保证 `chunk_markdown` 的 `\n\n` 分隔符有效；
- 单文件限制：最大 Markdown 5 MB、最大 chunk 数 5000、最大图片 300 张。

#### 5.5.2 RapidOCRParser（本地 OCR）

按 Yuxi 的真实调用方式落地（`rapid_ocr.py` 已核验）：

- 依赖：`rapidocr>=3.0`、`onnxruntime`、`pypdfium2`、`Pillow`（可选安装；本机 onnxruntime 1.31 已具备，rapidocr 3.10 可装）；
- 初始化：`RapidOCR(params={Global.model_root_dir, Det/Cls/Rec.engine_type=ONNXRUNTIME, lang=CH, model_type=MOBILE, ocr_version=PPOCRV5})`，**延迟到首次解析时加载**；
- 模型目录优先级：`RAPIDOCR_MODEL_DIR` → rapidocr 包内 `models/`（可写时）→ `~/.cache/rapidocr/models`；首次调用会下载 PP-OCRv5 ONNX 模型（健康检查不触发下载）；
- PDF：`pypdfium2` 逐页 `page.render(scale=2).to_pil()` → OCR → 结果 `result.txts` 用 `\n` 连接，页间 `\n\n`；**逐页串行、不保存页面图**；
- 图片：直接 OCR（先落临时 png，必要时再删）；
- 输出：`## 第 N 页\n\n{text}`；无表格/图片/版面结构；
- 健康：只检查 import 与模型目录可写，返回 `healthy/unavailable + reason`，不加载模型；
- 并发注意：Yuxi 的全局单例 `RapidOCR` 实例**没有加锁**，多线程同时调用安全性未保证；PTDRA 默认单 worker（`upload_concurrency=1`），如提高并发需为每个线程建实例或加锁；
- 失败提示：未安装依赖时返回 `available=false, reason="未安装 RapidOCR 依赖（rapidocr/onnxruntime），可改用 MinerU/PaddleX 或安装 backend/requirements-ocr.txt"`；
- 适用：扫描 PDF / 图片的快速兜底；不适合表格密集型文档。

#### 5.5.3 MinerUParser（HTTP 服务）

- 配置：`MINERU_API_URI`（默认 `http://127.0.0.1:30001`）、`MINERU_TIMEOUT_SECONDS`（默认 600）；
- 调用：同步 `requests.post(f"{uri}/file_parse", files={"files": (name, f, "application/octet-stream")}, data={...}, timeout=...)`，参数与 Yuxi 一致：
  `lang_list=["ch"]`、`backend="hybrid-auto-engine"`（可配）、`parse_method="auto"`、`formula_enable=True`、`table_enable=True`、`image_analysis=True`、`start_page_id=0`、`end_page_id=99999`、`return_md=True`、`response_format_zip=True`、`return_images=True`；
- 响应：ZIP → 安全解压（校验绝对路径/`..`，限制条目数与总解压量）→ 优先 `full.md`（否则第一个 `.md`）→ 图片目录候选 `<md_dir>/images`、`<md_dir>/../images`、`images` → 图片落盘 `assets/` → Markdown 链接改为 `assets/...`；
- 注意：Yuxi 把整个 ZIP 读进内存（`response.content`），PTDRA 改为**流式写临时文件**并加上限；
- 超时：`requests` 超时不是总时限，worker 层再加单文件总时限；
- 健康：`GET {uri}/openapi.json`，5s 超时，且 paths 含 `/file_parse`；
- 错误映射：非 200→`http_{status}`、超时→`timeout`、连接失败→`connection_error`、其他→`processing_failed`；
- 优点：表格/公式/图片质量最好；缺点：服务重、首次模型加载慢、同步长请求；
- 许可证：MinerU 3.1+ 为 Apache-2.0 + 附加条款（商用/大规模需评估）。

#### 5.5.4 PaddleXParser（PP-StructureV3 HTTP）

- 配置：`PADDLEX_URI`（默认 `http://127.0.0.1:8080`）、`PADDLEX_TIMEOUT_SECONDS`（默认 300）；
- 调用：`POST {uri}/layout-parsing`，JSON `{"file": <base64>, "fileType": 0(PDF)/1(图片), "useTableRecognition": true, "useFormulaRecognition": true, "useSealRecognition": false}`；
- 响应：`api_result["result"]["layoutParsingResults"][].markdown.text` 按页 `\n\n` 拼接；`errorCode != 0` 或字段缺失→报错；
- 图片：**Yuxi 不处理 `markdown.images`**；PTDRA 首版同样只保留文本/表格，若需要图片预览，必须额外下载并改写链接（列为 P2 可选增强）；
- 健康：`GET {uri}/health`（5s）；Yuxi 在**每次解析前**都先探测一次，PTDRA 改为使用 30s 健康缓存，避免批量文件额外 RTT；
- 超时：默认 300s；
- 适用：中文版面/表格强；Docker 镜像大，GPU 推荐。

#### 5.5.5 云 API（可选，P3）

- MinerU Official / PaddleOCR-VL / DeepSeek OCR：统一接口，但必须显式配置 Key 并提示“文件将发送到第三方”；
- `auto` 模式**绝不**自动选云引擎；只有用户显式选择才使用。

### 5.6 Markdown IR 规范

解析器输出必须满足以下约定，后续切片才能稳定：

1. 标题使用 ATX（`#`~`####`），层级连续；PPT 每页 `## 第 N 页：标题`；Excel 每个 sheet `## {sheet}`；PDF 每页 `## 第 N 页`。
2. 表格使用标准 Markdown 管道表；表头 + 分隔行 + 数据行；单元格内换行替换为空格。
3. 图片使用相对链接 `![caption](assets/image_001.png)`；不写绝对路径/外链；caption 优先取图注/alt，否则用文件名。
4. 代码块使用三反引号围栏；不要把 `#` 放进非代码文本。
5. 页码约定：优先用可见标题 `## 第 N 页`（PDF/PPT 天然按页）；当一页内容跨多个小节、无法用标题表达时，才插入 `<!-- page:N -->`。切片器先读标题页码，再读标记，写入 chunk 的 `page_no`；标记不进入最终展示。
6. 不保留导航/页眉页脚/水印等噪声（原生解析器尽量清理，MinerU/PaddleX 由服务处理）。

### 5.7 表格/图片感知切片（rich_chunker）

#### 5.7.1 为什么不能直接用 `chunk_markdown`

现有 `chunk_markdown` 是“按标题拆 + 递归字符切”：

- 大表格会被 `\n` 分隔符切断，后续片段没有表头，LLM 无法理解列含义；
- 图片 Markdown 链接会作为普通文本进入向量与 LLM 上下文，既浪费 token 又可能产生噪声；
- 图片本身没有任何可检索的语义块。

#### 5.7.2 新切片器接口

~~~python
# backend/app/core/rich_chunker.py
def chunk_uploaded_document(
    text: str,
    file_meta: dict | None = None,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> list[dict]:
    ...
~~~

输出 chunk 结构与现有完全一致：`{"text": str, "metadata": {...}}`；新增元数据：

| 字段 | 取值 | 用途 |
| --- | --- | --- |
| `content_type` | `text / table / image` | 区分块类型；前端可展示徽章；未来可调检索权重 |
| `table_index` | 文档内表格序号 | 表格去重/展示 |
| `image_index` | 文档内图片序号 | 图片去重/展示 |
| `asset_name` | `image_001.png` | 预览时定位资源 |
| `page_no` | 页码（可选） | 引用定位 |
| `row_count/col_count` | 表格行列数（可选） | 诊断 |

#### 5.7.3 算法

~~~text
1. 用标题栈把 Markdown 拆成 sections（复用 chunker._split_sections 的逻辑，
   重构为公开函数 split_markdown_sections，行为不变）。
2. 对每个 section 的内容按块扫描（跳过代码围栏）：
   - heading 行：并入文本缓冲
   - 连续以 | 开头的行：TABLE 块
   - ![alt](src)：IMAGE 块
   - 其他：TEXT 块（连续文本合并）
3. TEXT 块：用 RecursiveCharacterTextSplitter（512/64，中文分隔符）切分；
   每个 chunk 带当前 heading_path。
4. TABLE 块：
   - 若整表长度 <= chunk_size：一个 chunk；
   - 否则按数据行分组，每组都重复“表头 + 分隔行”，并在文本前加“（续表）”；
   - chunk 的 heading_path 为当前标题；content_type=table。
5. IMAGE 块：
   - 生成 figure chunk：text = “图 N：{caption}” + OCR 文本（若有）；
   - 若 caption/OCR 都为空：text = “图片附件：{filename}（位于 {heading_path}）”；
   - metadata 带 asset_name/image_index/page_no；content_type=image。
6. 每个 section 内 chunk_index 从 0 递增；total_chunks 为该 section 产出的块数
   （与现有 chunk_markdown 语义一致）；最后由 prepare_chunks 统一补 global_index。
~~~

#### 5.7.4 与现有管道的衔接

为不改变旧行为，给 `prepare_chunks` / `ingest_files` 增加**可选**参数：

~~~python
def prepare_chunks(..., chunker=None):
    chunk_fn = chunker or chunk_markdown
    chunks = chunk_fn(file_record["content"], file_meta, chunk_size=..., chunk_overlap=...)
    ...

def ingest_files(..., chunker=None):
    chunks = prepare_chunks(record, ..., chunker=chunker)
~~~

- 现有调用不传 `chunker` → 行为完全不变；
- 上传管道传 `chunker=chunk_uploaded_document`；
- `prepare_chunks` 额外合并 `file_record["extra_metadata"]`（doc_format/upload_id/parse_engine/original_filename），仍走 `sanitize_metadata` 标量清洗；
- 合并顺序：先固定字段，再 `extra_metadata.update()`，且**禁止覆盖** `file_path/source_type/title/content_hash/global_index` 等保留键（实现时显式剔除）；
- 现有测试断言 metadata 集合不变；新增测试只断言额外键存在。

### 5.8 索引集成

#### 5.8.1 写入

上传管道完成解析后，构造 `file_record`：

~~~python
record = {
    # 复用 note_scanner.normalize_file_path，保证与现有入库路径口径一致
    # （相对 backend 的 POSIX 路径，如 data/uploads/u_.../original.pdf）
    "file_path": normalize_file_path(original_path),
    "source_type": "tech_doc",
    "title": Path(original_filename).stem,
    "tags": tags,
    "source_url": "",
    "origin": "upload",
    "content": parsed.markdown,
    "extra_metadata": {
        "upload_id": upload_id,
        "doc_format": fmt,
        "parse_engine": parsed.engine,
        "original_filename": original_filename,
    },
}
summary = ingest_files(
    [record], state.chroma, state.metadata,
    chunker=chunk_uploaded_document,
    on_file_ingested=...,   # 可选
)
~~~

- `file_path` 指向真实落盘的原文件（不是 parsed.md），这样来源卡显示原始文件名，且原文件可下载；路径必须用 `normalize_file_path()` 归一化，避免绝对/相对路径重复入库；
- `content_hash` 仍是 Markdown 的 SHA-256（解析版本变化 → 内容变化 → 重新入库）；
- Chroma 向量 ID 仍为 `file_path#global_index#hash12`，天然幂等；
- 图片/表格 chunk 的 `asset_name` 是标量，可直接写入 Chroma metadata。

#### 5.8.2 删除/重入

- **手动 reindex / 修改 chunk_size 或 chunker 后必须传 `force_reingest=True`**：`ingest_files` 的跳过条件是“Markdown 内容哈希未变”，内容没变但切片策略变了时，不 force 会静默跳过；
- 重入前：按 `documents.id` 查旧 chunk ids → `delete_by_ids`；再按 `source_type + file_path` 删残留；
- 若上次在“向量已写、MySQL 未写”处失败，`ingest_files` 查不到 document，会留下孤儿向量；上传管道在解析/索引前额外执行一次 `delete_by_metadata("tech_doc", {"file_path": record_path})`，保证幂等；
- 删除上传：删向量 → 删 document（级联 chunks）→ 删 upload_assets/upload_files → 删目录；
- 解析失败：保留 upload_files 记录与原始文件，状态 failed，可重试；重试复用 upload_id，先清理 `parsed.md/assets`。

#### 5.8.3 BM25 与检索评分刷新

现有 `documents.py` 在批次结束后做：

~~~python
bm25.load_from_chroma(chroma)
retrieval_scorer = build_retrieval_scorer(metadata)
~~~

上传管道复用同一逻辑。建议抽成 `app/state.py` 的公共函数：

~~~python
def refresh_after_ingest(state) -> None:
    state.bm25.load_from_chroma(state.chroma)
    state.retrieval_scorer = build_retrieval_scorer(state.metadata)
~~~

`documents.py` 与上传 worker 都调用它，避免两套刷新逻辑漂移。

#### 5.8.4 来源合并守卫

在 `merge_chunks_to_sources` 构建 `tech_by_title` 时加守卫（注意不是加在 personal_note 循环里）：

~~~python
for proto in prototypes:
    if proto["source_type"] == "tech_doc" and proto.get("origin") != "upload":
        nt = _normalize_title(proto["title"])
        if nt and nt not in tech_by_title:
            tech_by_title[nt] = proto
~~~

旧数据 `origin` 为空或 `web_search`，行为不变；只有上传文档被排除在 hybrid 配对之外。

来源卡透传（否则前端拿不到 `upload_id`）：

1. `prepare_chunks` 的 `file_meta` 合并 `extra_metadata`（含 `upload_id/doc_format`），并随 chunk 元数据写入 Chroma；
2. `rag_service._build_card()` 目前只挑选固定字段，需要把 `upload_id`、`doc_format` 一并放入返回 dict（缺省为空串，向后兼容）；
3. `models/schemas.py::SourceItem` 增加 `upload_id: str | None`、`doc_format: str | None`；
4. 前端 `types/chat.ts::SourceItem` 同步增加可选字段，`SourceCard` 仅在 `origin==="upload"` 时使用。

测试点：旧来源卡字段不变；上传来源卡能拿到 `upload_id` 并打开预览；hybrid 不会把 upload 合并进去。

### 5.9 API 设计

新增 `backend/app/routers/uploads.py`，`prefix="/api/uploads"`，在 `main.py` 注册。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/api/uploads` | multipart 上传 1~20 个文件；创建批次与文件记录；提交执行器 |
| GET | `/api/uploads` | 文件列表（status/format/keyword 过滤，分页） |
| GET | `/api/uploads/{upload_id}` | 单文件详情（状态、引擎、chunk 数、错误、assets） |
| GET | `/api/uploads/{upload_id}/markdown` | 返回解析后 Markdown（图片链接改写为可访问 URL） |
| GET | `/api/uploads/{upload_id}/original` | 下载/预览原始文件 |
| GET | `/api/uploads/{upload_id}/assets/{name}` | 图片资源（路径校验） |
| POST | `/api/uploads/{upload_id}/reparse` | 重新解析（清空 parsed/assets，重置状态） |
| POST | `/api/uploads/{upload_id}/reindex` | 仅重切片+重入库（复用已解析 Markdown） |
| DELETE | `/api/uploads/{upload_id}` | 删除文件、向量、元数据、目录 |
| GET | `/api/uploads/tasks/{task_id}` | 批次进度与每文件状态 |
| GET | `/api/uploads/engines` | 引擎能力与健康状态（?refresh=true 强制重探） |

#### 5.9.1 上传请求

~~~text
POST /api/uploads
Content-Type: multipart/form-data

files: File[]                 # 必填，1~20 个
engine: auto|native|rapidocr|mineru|paddlex   # 默认 auto
auto_index: true|false        # 默认 true
chunk_size: int               # 可选，默认 512
chunk_overlap: int            # 可选，默认 64
tags: string                  # 可选，逗号分隔
force: true|false             # 可选，重复内容也重新入库
~~~

响应 202：

~~~json
{
  "task_id": "t_20261023_xxx",
  "files": [
    {
      "upload_id": "u_20261023_ab12cd34",
      "filename": "FastAPI 官方文档.pdf",
      "format": "pdf",
      "size": 1834201,
      "status": "queued"
    }
  ]
}
~~~

#### 5.9.2 错误码

| HTTP | 场景 | detail 示例 |
| --- | --- | --- |
| 400 | 不支持的扩展名/空文件/参数非法 | “不支持的文件类型 .exe；支持：pdf/docx/...” |
| 409 | 整批文件全部命中重复内容且 `force=false` | “已存在相同内容的文档（upload_id=...）；如需覆盖请删除后重传或传 force=true” |
| 413 | 超过 100 MB | “文件超过 100 MB 限制” |
| 422 | 指定引擎不支持该格式 | “MinerU 不支持 .xlsx” |
| 503 | 指定引擎不可用且无降级 | “MinerU 服务不可达；请启动服务或改用 auto/rapidocr” |
| 404 | upload_id/task_id 不存在 | — |

补充：

- 多文件请求中**部分重复**时仍返回 202；重复文件在 `files[]` 里标记 `status="duplicate"`，不进入执行器，其余文件正常处理。
- `GET /api/uploads/engines` 这类静态路径必须在 `GET /api/uploads/{upload_id}` **之前**注册，否则会被动态段吞掉（FastAPI 按声明顺序匹配）。
- `markdown` 接口需把 `assets/...` 重写为 `/api/uploads/{upload_id}/assets/...`，否则前端图片 404。

### 5.10 异步任务与状态机

#### 5.10.1 状态

~~~text
upload_files.status:
queued → parsing → parsed → indexing → indexed
                 ↘ failed_parse       ↘ failed_index
（重试从 failed_* → queued）

upload_tasks.status:
queued → running → completed | partial | failed
~~~

- `partial`：至少一个文件成功、至少一个失败；
- `failed`：全部失败；
- `progress`：0~100，按阶段更新（解析 5~50、资源 50~60、切片 60~75、索引 75~100）；
- 每个阶段写 `updated_at`，前端轮询可见。

#### 5.10.2 执行器

- 在 `AppState` 增加 `upload_executor = ThreadPoolExecutor(max_workers=settings.upload_concurrency, thread_name_prefix="upload")`；
- `POST /api/uploads` 只做校验、落盘、建记录，然后 `executor.submit(process_upload_batch, task_id)`；
- 解析/切片/入库全是同步代码，在 worker 线程中执行；不阻塞事件循环；
- 默认 `upload_concurrency=1`，避免多个 OCR 同时占满内存/GPU；可配到 2；
- `main.py` 的 lifespan 在 `yield` 后调用 `shutdown(wait=False)`。

#### 5.10.3 启动恢复

- 启动时 `UploadStore.recover_stale()`：
  - `queued` 且无执行中的任务 → 重新提交；
  - `parsing/indexing` 且超过 30 分钟 → 标记 `failed`，错误“服务重启中断，请重试”；
  - `parsed` 且 `auto_index=true` → 可自动继续索引（可选）；
- 恢复逻辑只操作 upload 表，不影响现有 `ingest_tasks`。

#### 5.10.4 单文件隔离

- 每个文件 try/except，失败写 `error_message`，继续下一个；
- 批次结束汇总 `processed_files/failed_files`，更新 task 状态；
- 引擎异常信息原样记录（含服务地址/超时/HTTP 状态），便于排查。

### 5.11 前端设计

#### 5.11.1 入口

- `ChatContainer` header 增加 “知识库” 按钮（带未完成上传数徽标）；
- 打开右侧 `KnowledgeBasePanel` 抽屉（复用 CitationPanel 的玻璃风格与滑入动画）；
- 抽屉三个页签：
  1. **上传**：拖拽区 + 文件列表 + 引擎选择 + “自动入库”开关 + 开始上传；
  2. **文档**：已上传文件列表（名称/格式/大小/状态/块数/引擎/操作）；
  3. **引擎**：RapidOCR/MinerU/PaddleX 健康状态与配置提示。

#### 5.11.2 上传流程

1. 选择文件后本地校验扩展名/大小，展示待上传列表；
2. 点击上传 → `FormData` POST `/api/uploads`；
3. 拿到 `task_id` 后每 1s 轮询 `/api/uploads/tasks/{task_id}`，直到 completed/partial/failed；
4. 每文件展示状态 chip 与进度条；失败显示 `error_message` + “重试”；
5. 抽屉关闭时停止轮询；重新打开时拉取 `/api/uploads?limit=50`。

#### 5.11.3 预览

- 文档列表“预览” → `UploadPreviewDialog`：
  - 拉取 `/api/uploads/{id}/markdown`；
  - 用已有 marked + DOMPurify 渲染（与 MessageBubble 一致）；
  - 相对图片链接 `assets/x.png` 重写为 `/api/uploads/{id}/assets/x.png`；
  - 提供“打开原文件”按钮（`/original`）。
- 来源卡 `SourceCard`：
  - `origin==="upload"` 时标签显示“上传文档”，点击不再调用 `/api/files/open`，而是打开预览/原文件；
  - 新增可选 props/字段：`upload_id`、`doc_format`；
  - 旧来源类型（tech_doc/personal_note/hybrid）分支保持原样。

#### 5.11.4 前端类型与 API

- `types/chat.ts`：`SourceItem` 增加 `upload_id?: string; doc_format?: string;`，`origin` 联合类型增加 upload；
- 新增 `types/upload.ts`：`UploadFileItem / UploadTask / EngineInfo`；
- `api/client.ts`：新增 `createUploads / listUploads / getUploadTask / reparseUpload / reindexUpload / deleteUpload / getUploadEngines / getUploadMarkdown`；
- 上传请求不要手写 `Content-Type`，让浏览器自动带 boundary。

### 5.12 配置项

在 `backend/app/config.py` 增加（Pydantic Settings 自动读 `.env`）：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `upload_enabled` | true | 总开关；关闭时路由返回 503，不影响现有功能 |
| `upload_dir` | `BACKEND_DIR/data/uploads` | 上传根目录（与 `notes_dir/tech_docs_dir` 同口径） |
| `upload_max_mb` | 100 | 单文件上限 |
| `upload_max_files_per_batch` | 20 | 单批文件数 |
| `upload_max_pages` | 500 | PDF 页数上限 |
| `upload_max_images` | 300 | 单文档图片上限 |
| `upload_max_chunks` | 5000 | 单文档 chunk 上限 |
| `upload_max_markdown_chars` | 5000000 | 解析后 Markdown 字符上限 |
| `upload_concurrency` | 1 | 解析/索引并发 |
| `upload_parse_timeout_seconds` | 600 | 单文件解析超时 |
| `upload_pdf_text_min_chars` | 80 | PDF 文本层判定阈值（字符/页） |
| `upload_image_ocr` | false | 是否对文档内图片再跑 OCR |
| `upload_engine_health_ttl_seconds` | 30 | 引擎健康缓存 |
| `rapidocr_model_dir` | `<BACKEND_DIR>/data/models/rapidocr` | OCR 模型目录（对应 `RAPIDOCR_MODEL_DIR`，可被环境变量覆盖） |
| `mineru_api_uri` | http://127.0.0.1:30001 | MinerU 服务 |
| `mineru_timeout_seconds` | 600 | MinerU 超时 |
| `paddlex_uri` | http://127.0.0.1:8080 | PaddleX 服务 |
| `paddlex_timeout_seconds` | 300 | PaddleX 超时 |

### 5.13 依赖管理

#### 5.13.1 基础依赖（必需）

加入 `backend/requirements.txt`：

~~~text
python-multipart>=0.0.9      # FastAPI 文件上传（当前未安装）
pypdf>=6.0                   # PDF 文本层（6.21.2 已验证有 cp312 win wheel）
python-docx>=1.1             # DOCX（1.2.0 已验证）
python-pptx>=1.0             # PPTX（1.0.2 已验证）
openpyxl>=3.1                # XLSX（3.1.5 已验证）
pandas>=2.2                  # CSV/XLS（3.1.0 已验证）
xlrd>=2.0                    # 老 XLS（2.0.2 已验证）
markdownify>=1.1             # HTML → Markdown（1.2.3 已验证）
tabulate>=0.9                # DataFrame.to_markdown 的运行时依赖（易漏）
~~~

`beautifulsoup4` 已在依赖中；`Pillow` 由 `python-pptx` 传递引入，如需缩略图可显式加。

说明：这些包**当前 venv 均未安装**（本轮只做了 wheel 可用性 dry-run），P0 开始前统一 `pip install -r backend/requirements.txt`，并先跑既有 318 条测试确认无版本冲突。

#### 5.13.2 可选依赖（OCR）

新建 `backend/requirements-ocr.txt`：

~~~text
rapidocr>=3.9,<4
onnxruntime>=1.20
pypdfium2>=5.0
Pillow>=10.0
~~~

- 不放进主 requirements：OCR 依赖体积大（opencv-headless 约 44MB + onnxruntime），且 CI/服务器未必需要；
- 本机已具备 **onnxruntime 1.31.0**，`rapidocr 3.10.0` / `pypdfium2 5.14.0` 均有 cp312-win wheel（已 dry-run 验证）；
- 首次调用会下载 PP-OCRv5 ONNX 模型到 `RAPIDOCR_MODEL_DIR`；`available()` 只做 import + 目录可写检查，**不加载模型、不触发下载**；
- 未安装时 RapidOCR 返回 `available=false`，其他引擎照常；`auto` 链会跳过它。

#### 5.13.3 外部服务（可选）

- MinerU：Docker（Yuxi 的 `docker/mineru.Dockerfile` 可参考），暴露 30001；
- PaddleX：Docker（`docker/paddlex.Dockerfile` + `PP-StructureV3.yaml`），暴露 8080；
- 也可指向局域网/远程机器，只需配置 URI；不要求与 PTDRA 同机。

### 5.14 安全与资源限制

| 风险 | 对策 |
| --- | --- |
| 路径穿越 | 只用 upload_id 建目录；文件名安全化；所有路径 `resolve` 后校验在 uploads 根下 |
| 超大文件 | 流式读 + 边读边计数；超过 100 MB 立即 413 并删除临时文件 |
| ZIP 炸弹 | 限制条目数 1000、解压总量 500 MB、拒绝绝对路径/`..`/符号链接 |
| 恶意 HTML/脚本 | BeautifulSoup 去 script；Markdown 预览 DOMPurify；不执行上传文件 |
| 宏文档 | 只允许 `.docx/.pptx/.xlsx`；`.docm/.xlsm` 拒绝 |
| PDF 加密/损坏 | pypdf 捕获 `PdfReadError`，明确报错 |
| 引擎超时 | requests 超时 + 解析器超时；失败降级/标记失败 |
| 云引擎隐私 | `auto` 不选云；显式选择时前端提示“文件将发送到第三方” |
| 磁盘占满 | 捕获 OSError；失败清理半成品；提供删除/统计接口 |
| 事件循环阻塞 | 解析全部放 worker 线程；上传读取用 `await file.read` 分块 |
| 并发打爆内存 | 默认 1 个 worker；OCR 模型常驻但串行调用 |

超限时的明确行为（不做静默截断）：

- Markdown 超过 `upload_max_markdown_chars` → 解析失败，错误信息给出实际字符数与上限；
- 预计 chunk 数超过 `upload_max_chunks` → 先切分再检查，超限则**不写库**，状态 `failed_index`，提示“请拆分文档或调大 upload_max_chunks”；
- PDF 超过 `upload_max_pages` → 解析前拒绝，避免无效 OCR；
- 图片超过 `upload_max_images` → 保留前 N 张，其余在 Markdown 中替换为文字占位，并在 `warnings` 记录（图片属于附属资源，截断比整篇失败更合理）。

### 5.15 兼容性与回退

- **功能开关** `upload_enabled`：关闭后新路由 503，现有功能零影响；
- **不改现有 API 语义**：`/api/documents/ingest`、`/api/chat/stream`、`/api/files/open` 行为不变；
- **不改现有集合/枚举**（方案 A）：Chroma 旧数据直接可读；MySQL 只新增表；
- **可选参数默认值**：`chunker=None`、`extra_metadata=None`（内部转 `{}`）保证旧调用不变；
- **前端旧数据**：`origin` 缺失时按旧逻辑（tech_doc+url → 网页；否则本地打开）；
- **上传文档的可见性**：因为复用 `tech_doc`，上传文档会出现在现有“技术文档”列表/统计中；这是预期行为（检索也应覆盖）。若产品希望分开，前端按 `origin==="upload"` 过滤即可，后端无需改；
- **回退步骤**：删除新路由注册/关闭开关 → 旧功能恢复；新表与新文件可保留，不影响旧链路；
- **测试基线**：实施前先跑一遍现有 `pytest` + `npm test` + `npm run build`，记录通过数；每阶段结束对比。

---

## 6. 实施计划

### P0 基础骨架 + 原生解析 + 上传 API（4~6 人日）

**目标**：不依赖任何外部服务，上传 md/txt/pdf/docx/pptx/xlsx/csv 即可完成解析、切片、入库、引用。

任务：

1. 新增 `config.py` 配置项、`upload_dir` 初始化；
2. 新增 `upload_tasks/upload_files/upload_assets` ORM 与 `UploadStore`；
3. 新增 `parsers/base.py / registry.py / native.py / zip_safe.py`；
4. 原生解析器：md/txt/html/pdf/docx/pptx/xlsx/xls/csv/json；
5. 新增 `rich_chunker.py`（先支持表格/图片基础块）；
6. 修改 `prepare_chunks/ingest_files` 支持 `chunker/extra_metadata`；
7. 新增 `upload_pipeline.py`：校验→落盘→解析→切片→`ingest_files`→状态；
8. 新增 `routers/uploads.py` 与 `main.py` 注册；
9. 前端：`KnowledgeBasePanel + UploadDropzone + UploadList` 最小可用；
10. 测试：原生解析、上传 API、管道集成、现有回归。

**出口**：上传 PDF/DOCX/PPTX/XLSX/MD → 列表可见 → 问答能检索到并显示“上传文档”来源卡。

### P1 表格/图片切片 + RapidOCR + PDF 自动判定（3~5 人日）

1. 完善 `rich_chunker`：表格超长重复表头、figure chunk、`content_type` 元数据、`page_no`；
2. RapidOCR 适配器 + 可选依赖文件 + 健康检查 + Python 版本兼容提示；
3. PDF 文本层检测 + 自动路由 + 失败降级；
4. 图片落盘、Markdown 链接重写、`asset_store`；
5. 来源卡上传分支 + 预览对话框（Markdown + 图片）；
6. 测试：大表格、图片 chunk、扫描 PDF（mock）、引擎降级。

**出口**：扫描 PDF 用 RapidOCR 可入库；表格不被切碎；来源卡可预览解析文本/图片。

### P2 MinerU / PaddleX 适配 + 高精度版面（4~6 人日）

1. `mineru_engine.py`：`/file_parse` + ZIP 安全解压 + 图片改链 + 超时/健康；
2. `paddlex_engine.py`：`/layout-parsing` + 每页 Markdown + 可选图片；
3. 引擎选择器 UI + `/api/uploads/engines` 健康页签；
4. 云 API 适配（可选，P3 前半）；
5. 测试：Mock ZIP/JSON、服务不可达降级、图片/表格质量验收。

**出口**：配置 `MINERU_API_URI` 或 `PADDLEX_URI` 后，上传扫描 PDF 能得到含表格/图片的高质量 Markdown；服务不可用时自动回退。

### P3 加固、测试与验收（3~4 人日）

1. 启动恢复、失败重试、删除清理、配额/统计；
2. 安全测试：路径穿越、ZIP 炸弹、超大文件、宏格式；
3. 前端测试：上传状态机、轮询、预览、来源卡；
4. 文档：README/PRD 附录/API 说明；更新 `init_mysql.sql`；
5. 全量回归 + 10 条验收。

**总量**：14~21 人日；MVP（P0+P1）约 8~10 人日。

### 6.1 PR 拆分与实施顺序（每个 PR 独立可回归）

| PR | 内容 | 依赖 | 验收 |
| --- | --- | --- | --- |
| PR-1 | 配置 + `upload_*` 三张表 + `UploadStore` + 执行器骨架 + lifespan 启停 | 无 | 新表自动创建；旧测试全绿 |
| PR-2 | `parsers/base.py` + `registry.py` + `native.py` + `zip_safe.py` + 单测 | PR-1 | md/txt/pdf/docx/pptx/xlsx/csv/xls/html/json 解析测试 |
| PR-3 | `rich_chunker.py` + `prepare_chunks/ingest_files` 可选参数 + 单测 | PR-2 | 旧切片测试不变；表格/图片块断言 |
| PR-4 | `upload_pipeline.py` + `routers/uploads.py` + 状态机 + 启动恢复 + API 测试 | PR-1~3 | 上传→解析→入库→检索集成测试 |
| PR-5 | 前端 `KnowledgeBasePanel` + 上传/列表/预览 + 来源卡分支 + vitest | PR-4 | 80 条旧前端测试 + 新增测试全绿；build 通过 |
| PR-6 | `rapidocr_engine.py` + PDF 文本层判定 + `auto` 降级 + mock 测试 | PR-2,4 | 扫描 PDF（mock）可入库；引擎不可用有明确提示 |
| PR-7 | `mineru_engine.py` + `paddlex_engine.py` + 健康页签 + mock ZIP/JSON | PR-6 | 不可达时降级；ZIP 安全用例 |
| PR-8 | 删除/重试/配额/安全加固 + 文档 + 全量验收 | 全部 | 10 条验收 + 安全用例 |

顺序原则：**先让“原生解析 + 上传 + 旧链路”闭环（PR-1~5），再接 OCR/外部引擎（PR-6~7）**；每个 PR 合并前必须跑 `pytest` + `npm test` + `npm run build`，并记录通过数（基线 318 / 80）。

---

## 7. 测试计划

### 7.1 后端

| 类型 | 文件 | 覆盖 |
| --- | --- | --- |
| 单元 | `tests/test_parser_registry.py` | 能力表、未知引擎、懒加载、健康检查缓存 |
| 单元 | `tests/test_native_parsers.py` | 各格式标题/段落/表格/图片；无内容丢失；编码兜底 |
| 单元 | `tests/test_rich_chunker.py` | 大表格重复表头；figure chunk；content_type；与旧 chunk 语义一致 |
| 单元 | `tests/test_pdf_engine_choice.py` | 文本层阈值、加密 PDF、扫描件降级 |
| 单元 | `tests/test_ocr_adapters.py` | Mock RapidOCR/MinerU ZIP/PaddleX JSON；超时/HTTP 错误；图片改链 |
| 集成 | `tests/test_upload_api.py` | multipart、大小/类型/重复 409、列表、状态、删除 |
| 集成 | `tests/test_upload_pipeline.py` | 上传 → 解析 → 切片 → Chroma/MySQL 数量一致；BM25 可检索；重入幂等 |
| 回归 | 现有全部 | 旧入库/检索/SSE/来源卡不变 |

测试夹具：

- 小型 `.docx/.pptx/.xlsx` 用 python-docx/python-pptx/openpyxl 在 fixture 中生成；
- PDF 用固定小样本（文本层 + 扫描版各一）或 mock；
- MinerU ZIP/PaddleX JSON 用固定 JSON + 小图片；
- 引擎测试不依赖真实服务；真实服务冒烟单独标记 `pytest.mark.integration`。

### 7.2 前端

| 文件 | 覆盖 |
| --- | --- |
| `KnowledgeBasePanel.test.tsx` | 抽屉打开、文件选择、引擎切换、上传调用 |
| `useUploads.test.tsx` | 轮询停止、失败重试、卸载清理 |
| `UploadPreviewDialog.test.tsx` | Markdown 渲染、图片链接改写、DOMPurify |
| `SourceCard.test.tsx` | 新增 uploaded 分支；旧分支不回归 |

### 7.3 手工验收

- 真实上传 5 种格式各 1 个；
- 扫描 PDF 走 RapidOCR/MinerU；
- 关闭 MinerU 服务后 auto 仍能完成（降级）；
- 对话提问命中上传文档，来源卡显示“上传文档”，点击可预览；
- 删除上传后，检索不再命中，磁盘目录删除。

---

## 8. 验收标准

| 编号 | 验收项 | 判定 |
| --- | --- | --- |
| AC1 | 格式覆盖 | md/txt/pdf/docx/pptx/xlsx/csv/html 均可上传并入库；不支持格式给出明确提示 |
| AC2 | 引擎覆盖 | `/api/uploads/engines` 能看到 native/rapidocr/mineru/paddlex；指定引擎可用时走指定引擎；不可用时 auto 降级且不阻断 |
| AC3 | 文本抽取 | 解析后 Markdown 包含原文标题/段落/代码块；无内容丢失（抽样断言） |
| AC4 | 表格抽取 | DOCX/PPTX/XLSX 表格转为 Markdown 表；长表切分后每段有表头；PDF 表格在 MinerU/PaddleX 路径保留 |
| AC5 | 图片抽取 | DOCX/PPTX/XLSX 及 MinerU/PaddleX 路径的文档图片落盘 `assets/`；Markdown 链接可访问；figure chunk 可检索；删除时清理（原生 PDF 不承诺抽图） |
| AC6 | 切片与索引 | chunk 有 `heading_path/content_type/file_path/upload_id`；Chroma 数量与 MySQL chunks 一致；BM25 能召回 |
| AC7 | 状态与重试 | 列表能看到 queued/parsing/parsed/indexing/indexed/failed_parse/failed_index；失败可重试；服务重启后中间态收敛 |
| AC8 | 非回归 | 现有后端/前端测试全绿；旧来源类型引用/打开行为不变；启动不因 OCR 依赖失败 |
| AC9 | 安全 | 路径穿越、ZIP 炸弹、宏格式、超大文件均被拒绝且不残留半成品；预览经 DOMPurify |
| AC10 | 资源与恢复 | 加密/0 页 PDF 明确失败；单 worker 下内存稳定；重启后 queued 重投、processing 超时收敛 |

---

## 9. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| 服务器/CI 未装 OCR 依赖或模型未下载 | RapidOCR 不可用 | `available()` 惰性检测 + 明确提示；MinerU/PaddleX HTTP 兜底；可选首启预热接口 |
| MinerU/PaddleX 部署重 | 用户装不起来 | 原生解析为默认；服务可选；提供 Docker 命令与远程 URI |
| PDF 文本层质量差 | 原生解析乱码 | 字符数阈值 + 抽样；低质量自动 OCR；允许手动指定引擎 |
| 大表格/大文件 | 内存/Chroma 压力 | 页数/图片/chunk/Markdown 上限；流式读；表格按行切 |
| 图片语义检索弱 | 图片问题答不出 | v1 用 caption/OCR/文件名建 figure chunk；v2 可接多模态 embedding |
| 上传文档与笔记 hybrid 误合并 | 来源卡错误 | `origin=upload` 守卫；测试覆盖 |
| 失败留下孤儿向量 | 检索脏数据 | 入库前按 file_path 清残留；删除/重试幂等 |
| 进程重启丢任务 | 状态卡住 | 启动恢复：`queued` 重投、`parsing/indexing` 超时置 failed |
| MySQL 新表未建 | 上传报错 | `Base.metadata.create_all` 自动建；`init_mysql.sql` 同步更新；启动自检 |
| 隐私/合规 | 文件外发 | auto 不选云；云引擎显式选择 + 提示；本地优先 |
| OCR 实例并发不安全 | 结果错乱/崩溃 | 默认单 worker；如提高并发，每线程独立实例或加锁（Yuxi 共享实例无锁，不要照抄） |
| 解析器总时限不可中断 | 队列被长任务占住 | worker 用 `future.result(timeout=...)` 记失败；HTTP 用可配超时；解析器不共享可变状态 |
| 引用只能定位到 chunk | 用户无法跳到页码 | v1 在 Markdown 写 `<!-- page:N -->`，chunk 元数据带 `page_no`；前端展示“第 N 页” |

---

## 10. 待确认问题

### 已确认（本轮核验，无需再问）

- **运行环境**：`.venv` = CPython 3.12.7，onnxruntime 1.31.0 已安装，chromadb 1.5.9 正常；所有新增解析依赖均有 cp312-win wheel。
- **上传与解析解耦**：PTDRA 采用“一次请求内落盘 + 入队”，不照抄 Yuxi 的“先 `/files/upload` 再 `/documents`”两段式，避免孤儿对象。
- **去重**：按 `content_hash` 在**整个上传批次内**去重（不只查库）；部分重复返回 202 + `status="duplicate"`，整批全重复才 409。
- **引用粒度**：v1 精确到 chunk + 可选页码；不做 bbox 级高亮。

### 仍需你拍板（建议值已给出）

1. **来源类型**：是否接受“复用 `tech_doc` + `origin=upload`”（推荐，零迁移），还是必须独立 `uploaded_doc` 集合？后者需要改 Chroma 映射、检索 fan-out 与来源卡逻辑。
2. **引擎部署**：首版只要求 RapidOCR 本地（推荐），还是必须同时提供 MinerU/PaddleX 的 Docker 编排脚本？
3. **legacy Office**：是否需要 `.doc/.ppt`（需 LibreOffice 转换）？建议首版不支持，给明确提示。
4. **图片检索期望**：首版“图片 caption/OCR 文本 + 资源预览”是否可接受？还是必须图片向量（CLIP/多模态）？
5. **上传入口**：知识库抽屉是否满足，还是需要独立“知识库管理”页面？
6. **限额**：100 MB / 500 页 / 5000 chunk 是否符合你的文档规模？（Yuxi 为 100 MB，无页数/chunk 上限）

---

## 附录 A：Yuxi 关键代码索引

| 主题 | 文件 | 行 |
| --- | --- | --- |
| 解析器基类 | `knowledge/parser/base.py` | 全文 |
| 引擎能力表 | `knowledge/parser/capabilities.py` | `PARSER_CAPABILITIES` |
| 引擎工厂/懒加载 | `knowledge/parser/factory.py` | `DocumentProcessorFactory` |
| 格式路由 | `knowledge/parser/unified.py` | `parse_resolved_document` 第 116 行 |
| PDF OCR 路由 | `knowledge/parser/unified.py` | `parse_pdf` 第 56 行 |
| RapidOCR | `knowledge/parser/rapid_ocr.py` | `process_pdf` 第 175 行 |
| MinerU | `knowledge/parser/mineru.py` | `MinerUParser` 第 22 行 |
| PaddleX PP-StructureV3 | `knowledge/parser/pp_structure_v3.py` | `PPStructureV3Parser` 第 22 行 |
| ZIP 图片处理 | `knowledge/parser/zip_utils.py` | `process_zip_file` 第 21 行 |
| 文件解析状态机 | `knowledge/base.py` | `parse_file` 第 240 行 |
| 索引状态机 | `knowledge/base.py` | `index_file` 第 763 行 |
| Milvus 索引 | `knowledge/implementations/milvus.py` | `index_file` 第 678 行 |
| 切片分发 | `knowledge/chunking/ragflow_like/dispatcher.py` | `chunk_markdown` 第 70 行 |
| token/合并算法 | `knowledge/chunking/ragflow_like/nlp.py` | `count_tokens` 第 49 行、`naive_merge` 第 461 行 |
| 任务编排 | `services/knowledge_task_service.py` | `run_knowledge_ingest` 第 35 行 |
| OCR 配置解析 | `services/ocr_service.py` | `parse_document` 第 75 行 |
| 上传接口 | `server/routers/knowledge_router.py` | `/files/upload` 约第 715 行（暂存 MinIO） |
| 添加文档/入队 | `server/routers/knowledge_router.py` | `/databases/{kb_id}/documents` 第 1504 行；`/documents/add` 第 761 行 |
| 文件/块表 | `storage/postgres/models_knowledge.py` | `KnowledgeFile` 第 50 行、`KnowledgeChunk` 第 83 行 |

## 附录 B：PTDRA 改动清单

| 类型 | 文件 | 改动 |
| --- | --- | --- |
| 新增 | `backend/app/parsers/*` | 解析器抽象、注册表、native、RapidOCR、MinerU、PaddleX、ZIP 安全 |
| 新增 | `backend/app/core/rich_chunker.py` | 表格/图片感知切片 |
| 新增 | `backend/app/services/upload_store.py` | 上传任务/文件/资源 CRUD |
| 新增 | `backend/app/services/upload_pipeline.py` | 上传处理编排 |
| 新增 | `backend/app/services/asset_store.py` | 图片落盘/URL/清理 |
| 新增 | `backend/app/routers/uploads.py` | 上传 API |
| 修改 | `backend/app/config.py` | upload_* / engine_* 配置 |
| 修改 | `backend/app/state.py` | UploadStore、执行器、`refresh_after_ingest`（现有 `_on_ingested` 改为复用它） |
| 修改 | `backend/app/main.py` | 注册路由；关闭执行器 |
| 修改 | `backend/app/services/ingest_pipeline.py` | 可选 `chunker` / `extra_metadata` |
| 修改 | `backend/app/services/metadata_store.py` | 新增 UploadTask/UploadFile/UploadAsset ORM |
| 修改 | `backend/app/services/rag_service.py` | hybrid 合并排除 `origin=upload`；`_build_card` 透传 upload_id/doc_format |
| 修改 | `backend/app/models/schemas.py` | `SourceItem` 可选 upload_id/doc_format |
| 修改 | `backend/requirements.txt` + `.env.example` | 依赖与配置样例 |
| 新增 | `backend/requirements-ocr.txt` | RapidOCR 可选依赖 |
| 修改 | `frontend/src/...` | 抽屉、上传、预览、来源卡 |
| 可选 | `backend/scripts/init_mysql.sql` | 新增三张表 DDL |
| 不变 | `backend/app/core/chunker.py` / `retriever.py` / `confidence.py` / `question_scorer.py` | 默认行为不变 |

## 附录 C：curl 示例

~~~bash
# 上传（auto 引擎，自动入库）
curl -X POST http://localhost:8000/api/uploads \
  -F "files=@D:/docs/report.pdf" \
  -F "files=@D:/docs/design.docx" \
  -F "engine=auto" \
  -F "auto_index=true" \
  -F "tags=架构,规范"

# 查看任务
curl http://localhost:8000/api/uploads/tasks/t_20261023_xxx

# 查看文件列表
curl "http://localhost:8000/api/uploads?status=indexed&limit=20"

# 预览 Markdown
curl http://localhost:8000/api/uploads/u_20261023_ab12cd34/markdown

# 指定 MinerU
curl -X POST http://localhost:8000/api/uploads \
  -F "files=@D:/docs/scanned.pdf" \
  -F "engine=mineru"

# 引擎健康
curl http://localhost:8000/api/uploads/engines?refresh=true
~~~

## 附录 D：实施检查清单

- [ ] 现有 `pytest` / `npm test` / `npm run build` 基线通过并记录
- [ ] `upload_tasks/upload_files/upload_assets` 建表成功
- [ ] 原生解析 8 种格式冒烟
- [ ] PDF 文本层判定阈值验证（数字/扫描各 3 份）
- [ ] 表格长切分重复表头验证
- [ ] 图片落盘/预览/删除清理验证
- [ ] RapidOCR 可用性/降级验证
- [ ] MinerU/PaddleX 健康检查与降级验证
- [ ] 上传→检索→引用→预览 端到端
- [ ] 旧来源卡/旧检索/SSE 回归
- [ ] 安全用例（穿越/超大/ZIP/宏）通过
- [ ] 文档与 `init_mysql.sql` 更新

---

> 备注：本文为 **v1.0 可实施方案**（已对照 PTDRA 与 Yuxi 源码核验），**未修改任何业务代码**。实施前请先确认第 10 章的待拍板项，尤其是来源类型（方案 A/B）与引擎部署形态。

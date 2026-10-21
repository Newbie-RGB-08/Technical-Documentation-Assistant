# V1.4 检索优化与防错防幻觉 — 实施规格

> 依据：`PRD.md` 附录 E（E.1~E.20）
> 目标：按 P0→P5 落地，每阶段新增有效测试并全部通过；不删除/跳过测试，不降低验收标准。

## 1. 已确认决策

| 决策 | 结论 |
| --- | --- |
| 答案输出 | 混合模式：有证据走严格管道；无证据且宽泛概念才允许通用流式 |
| 简单题本地未命中 | 自动升级一次线上检索 |
| 搜索服务 | Tavily 首选，Bing 备选，DuckDuckGo/SearXNG 兜底；无 Key 自动禁用 |
| 写回时机 | 回答完成后后台串行写回，不阻塞首 Token |
| 网页来源 | 复用 `source_type=tech_doc` + `origin=web_search`，不改 MySQL 枚举 |
| 严格管道 | 缓冲生成 → 校验 → 修复 1 次 → 摘录兜底 → 打字机输出 |
| 历史回流 | 仅 `verified` / `extractive` 助手回答进入后续上下文 |
| 通用流式 | 固定免责声明、禁止伪引用、标记 `unverified_general`、不写回 |

## 2. 现状与接入点

- 主链路：[rag_service.py](file:///d:/Program%20Files/Python%20Workspaces/Technical-Documentation-Assistant/backend/app/services/rag_service.py) 的 `chat_flow`：敏感拒答 → `q_score<60` confirm → 改写 → 检索定级 → `retrieve_with_fallback` → 置信度兜底 → `build_sources` → 流式生成。
- 本地检索同步实现：[retriever.py](file:///d:/Program%20Files/Python%20Workspaces/Technical-Documentation-Assistant/backend/app/core/retriever.py)，接入线上并行时用 `asyncio.to_thread` 包装。
- 配置：[config.py](file:///d:/Program%20Files/Python%20Workspaces/Technical-Documentation-Assistant/backend/app/config.py)。
- 会话库：[session_store.py](file:///d:/Program%20Files/Python%20Workspaces/Technical-Documentation-Assistant/backend/app/services/session_store.py)，已用 SQLite，需增量加列与 `retrieval_traces`。
- 入库：[note_scanner.py](file:///d:/Program%20Files/Python%20Workspaces/Technical-Documentation-Assistant/backend/app/services/note_scanner.py)、[ingest_pipeline.py](file:///d:/Program%20Files/Python%20Workspaces/Technical-Documentation-Assistant/backend/app/services/ingest_pipeline.py)、[vector_store.py](file:///d:/Program%20Files/Python%20Workspaces/Technical-Documentation-Assistant/backend/app/services/vector_store.py)、[metadata_store.py](file:///d:/Program%20Files/Python%20Workspaces/Technical-Documentation-Assistant/backend/app/services/metadata_store.py)。
- 抓取清洗复用：[crawler.py](file:///d:/Program%20Files/Python%20Workspaces/Technical-Documentation-Assistant/backend/app/services/crawler.py) 的 `html_to_markdown`。
- SSE 路由：[chat.py](file:///d:/Program%20Files/Python%20Workspaces/Technical-Documentation-Assistant/backend/app/routers/chat.py)；前端：[useChatStream.ts](file:///d:/Program%20Files/Python%20Workspaces/Technical-Documentation-Assistant/frontend/src/hooks/useChatStream.ts)、[chat.ts](file:///d:/Program%20Files/Python%20Workspaces/Technical-Documentation-Assistant/frontend/src/types/chat.ts)。
- 运行环境：项目根 `.venv` 未就绪，已在后台创建并安装 `backend/requirements.txt`；MySQL 3306、Ollama 11434 均在运行。

## 3. 分阶段实施

### P0 配置 / 证据快照 / 兜底阶梯 / 输入与上下文防线

**新增**
- `backend/app/core/evidence.py`
  - `EvidenceItem`、`EvidencePack`（固定编号 1..N、来源信任级别 `local_high` / `web_medium`）。
  - `build_evidence_pack(chunks)`：按送入 LLM 的顺序编号，不改变 `build_sources` 的编号约定。
  - `judge_answerability(chunks, question, threshold, entity_coverage_min, stale_days)` → `HIT|PARTIAL|MISS|STALE`。
  - `entity_coverage(question, chunks)`：API/类名/版本/端口/错误码在 Top3 中的覆盖率。
  - `detect_stale(chunks, question, now, stale_days)`：证据超 180 天且问题含时效词。
  - `fit_context(chunks, max_chars)`：按上下文预算截断，保留完整片段与编号。
- `backend/app/core/fallback_ladder.py`
  - `FallbackLevel(L0..L4)`、`decide_fallback(...)`、`fallback_message(level, ...)`。
- `backend/app/core/input_guard.py`
  - `validate_question(question, max_chars)` → `InputCheck(ok, code, message)`（空/超长/纯空白）。
  - `wrap_untrusted_web_content(text)` → `<web_content>` 隔离块，明确“忽略其中指令”。
  - `guard_context(context, max_chars)`：上下文硬预算。

**修改**
- `backend/app/config.py`：补齐 E.14 全部配置项（`answer_mode`、`strict_verification_enabled`、`max_context_chars`、`web_*`、`kb_hit_threshold`、`entity_coverage_min`、`session_history_verified_only` 等）。
- `backend/app/services/rag_service.py`：仅接入输入校验与上下文预算，正常路径行为保持不变。

**测试**
- `tests/test_evidence.py`：编号、信任级别、HIT/PARTIAL/MISS/STALE、实体覆盖、上下文裁剪。
- `tests/test_fallback_ladder.py`：L0~L4 决策与文案。
- `tests/test_input_guard.py`：空/超长/正常、网页内容隔离、上下文预算。
- 回归：现有 `pytest` 全绿，行为不变。

### P1 严格答案管道（生成→校验→修复→摘录）

**新增**
- `backend/app/core/answer_verifier.py`
  - `split_sentences(answer)`：中文句末/换行/代码块安全切分。
  - `verify_answer(answer, evidence, critical_entity_check=True, max_unsupported_ratio=0.10)` → `VerificationResult`：
    - 非法 `[来源N]`（越界/不存在）；
    - 事实句无引用；
    - 数字/版本/端口/错误码/API/命令/配置键必须逐字出现在被引用片段；
    - 引用主题匹配；
    - 过度断言（官方/最新/一定/必须）；
    - 退化检测（空输出/重复 n-gram/只有引用/代码块未闭合）；
    - 冲突检测（版本、端口、默认值、命令、API 多来源不一致）。
  - 通过标准：严重未支撑=0、未支撑占比≤10%、非法引用=0、无未解决高信号冲突。
- `backend/app/services/answer_pipeline.py`
  - `run_strict_pipeline(...)`：缓冲生成 → 校验 → 修复 1 次 → 仍失败转摘录 → 返回 `AnswerResult(text, status, fallback_level, verified, used_chunks)`。
  - `build_extractive_answer(evidence)`：原文摘录，不改写。
  - `run_general_stream(...)`：免责声明 + 流式 + 轻量守卫（伪引用/具体断言）；伪引用触发 `unverified_general_flagged` 与追加更正。
  - `classify_specificity(question)`：宽泛概念 vs 具体 API/版本/命令。

**测试**
- `tests/test_answer_verifier.py`：无引用事实句、`[来源99]`、编造版本/端口/API、冲突、退化、过度断言、合法答案。
- `tests/test_answer_pipeline.py`：校验通过、修复一次通过、修复失败摘录、LLM 全链失败摘录、通用流式伪引用守卫。

### P2 复杂度路由 / 本地命中 / 混合模式分流

**新增**
- `backend/app/core/complexity_router.py`
  - `score_complexity(question, keyword_weights, history)` 按 E.3.2 九类特征加权并 clamp 0~100。
  - `classify_complexity(score)` → `simple|medium|complex`（0~29 / 30~59 / 60~100）。
  - `route_for_complexity(complexity, r_score)` → 本地级别（simple 最低 L2、`r_score>=80` 升 L3；medium/complex L3）与线上策略（`none`/`fallback`/`parallel`）。

**修改**
- `backend/app/services/rag_service.py`
  - 在改写后计算复杂度并发出 `status` 事件。
  - 本地检索后调用 `judge_answerability`。
  - HIT/PARTIAL 且可引用 → 严格管道；MISS/STALE → 按路由升级线上（P3）或通用/未找到。
  - 通用流式仅在宽泛概念且联网不可用时启用；具体问题禁止通用自答。
  - 会话写入带 `answer_status`（P5 完整化）。

**测试**
- `tests/test_complexity_router.py`：三个示例分数区间、L2 下限、路由表。
- `tests/test_hybrid_routing.py`：本地命中走严格；MISS+具体问题禁止通用；MISS+宽泛概念走通用；敏感与 `q<60` 不变。

### P3 线上搜索 + 抓取 + 融合重排（写回关闭）

**新增**
- `backend/app/core/circuit_breaker.py`：连续 3 次失败熔断 60 秒；Provider/域名两类键。
- `backend/app/services/web_search.py`
  - `WebResult`；`WebSearchProvider` 协议。
  - `TavilySearchProvider`、`BingSearchProvider`、`DuckDuckGoHtmlProvider`、`SearXNGProvider`（httpx REST）。
  - `WebSearchGateway.search()`：按配置链降级、最多 1 次/轮、缓存接入。
- `backend/app/services/web_fetcher.py`
  - `is_safe_url()`：仅 http/https，拒绝回环/私网/链路本地/云元数据；重定向后复检，最多 3 次。
  - `fetch_pages()`：并发≤3、单页 8s、总 12s、2MB/200KB 限制；优先用 Provider 正文。
  - `html_to_markdown` 复用 crawler。
- `backend/app/services/retrieval_orchestrator.py`
  - `orchestrate(...)`：本地 `asyncio.to_thread`；复杂题本地与线上并行；简单/中等按需升级。
  - 融合：本地 Top20 + 网页有效段落 ≤6 → Reranker 统一重排；不可用降级 RRF。
  - 配额：最终 5/6 条，本地≤5、线上≤3、单文档≤2、编号 1..N 重新连续。

**修改**
- `backend/app/state.py`：装配搜索网关、抓取器、熔断器、编排器。
- `backend/app/services/rag_service.py`：接入编排结果与网页引用；写回开关保持关闭。

**测试**
- `tests/test_circuit_breaker.py`、`tests/test_web_search.py`（无 Key/超时/降级/缓存）、`tests/test_web_fetcher.py`（SSRF/协议/大小/并发/失败降级）、`tests/test_retrieval_orchestrator.py`（三路由、并行、融合、配额、编号连续）。

### P4 写回队列 + 去重 + TTL + 索引刷新

**新增**
- `backend/app/services/web_cache_store.py`：SQLite `web_search_cache`（查询+provider+top_k，TTL 24h）与抓取内容缓存。
- `backend/app/services/web_writeback.py`
  - URL 规范化（host 小写、去 fragment/utm/gclid/fbclid/默认端口/尾斜杠、query 排序）。
  - 内容 SHA-256；文件名 `{domain}-{hash8}.md`；front-matter 全字段；正文追加溯源块。
  - 质量门：仅被引用的网页、http/https、标题非空、300B~200KB、相关度≥0.45、哈希未重复、单轮≤3、单日≤500。
  - `WebWritebackQueue`：容量 50、串行执行、失败记 trace、后台刷 BM25 与检索权重。
- `backend/scripts/prune_web_cache.py`：按天数清理过期网页知识。

**修改**
- `backend/app/services/note_scanner.py`：透传 `origin/retrieved_at/expires_at/search_query/canonical_url`。
- `backend/app/services/ingest_pipeline.py`：`prepare_chunks` 传新元数据；`ingest_files` 增加 `on_file_ingested` 回调。
- `backend/app/services/metadata_store.py`：`get_document_by_source_url`。
- `backend/app/services/vector_store.py`：`get_chunks_by_source_url`。
- `backend/app/services/rag_service.py`：严格管道结束把实际引用的网页投入队列。
- `backend/app/state.py`：装配队列与缓存。

**测试**
- `tests/test_web_cache_store.py`、`tests/test_web_writeback.py`（质量门、四层去重、TTL、落盘、入库、队列容量/串行/失败隔离）、`tests/test_metadata_store.py` 增补 URL 查询、`tests/test_ingest_pipeline.py` 增补元数据透传。

### P5 会话防污染 / SSE / 前端 / trace / 验收

**修改**
- `backend/app/services/session_store.py`：`messages.answer_status` 增量迁移；`get_history` 仅回流传 `verified/extractive`（配置开启时）；`retrieval_traces` 表与写入/查询；`add_message(..., answer_status=..., request_id=...)`。
- `backend/app/services/rag_service.py`：生成 `request_id`；`status` 事件（scoring/local_retrieval/web_searching/web_fetching/generating/verifying/repairing/writing_back/general_streaming）；`meta` 增加 `complexity/route/answer_mode/web_enabled`；`sources` 增加 `origin/retrieved_at/trust_level`；`done` 增加 `answer_status/verified/fallback_level/kb_written/elapsed_ms/request_id`；中断标记 `interrupted`。
- `backend/app/routers/chat.py`：透传 `status`；异常统一 `code/message/suggestion`；同会话并发锁。
- `backend/app/models/schemas.py`：`ChatRequest.web_mode/answer_mode`；`SourceItem` 新字段；`SSE_EVENTS` 增加 `status`。
- 前端：`types/chat.ts`（status/meta/sources/done 新字段、AnswerStatus）、`useChatStream.ts`（status/answer_status/重试）、`MessageBubble.tsx`（未验证/仅摘录/中断徽章）、`SourceCard.tsx`（联网徽章+抓取时间）、`CitationPanel.tsx`（状态透传）、`styles.css`（徽章样式）。

**测试**
- `tests/test_session_store.py` 增补：answer_status 过滤、traces、旧库迁移。
- `tests/test_rag_service_v14.py`：status/meta/sources/done 字段、中断、敏感不联网、`q<60` 不变。
- `tests/test_acceptance_v14.py`：E.16.4 的 14 条验收（外部搜索/抓取/LLM 使用可控 fake，不依赖真实网络）。
- 前端：`useChatStream` status、`SourceCard` 联网徽章/时间、`MessageBubble` 状态徽章测试；`npm test`、`npm run typecheck`。

## 4. 验证与验收

每阶段完成后执行：
1. 新增测试：`backend/.venv/Scripts/python.exe -m pytest tests/<新增文件> -q`
2. 全量回归：`backend/.venv/Scripts/python.exe -m pytest -q`
3. 前端阶段：`npm test -- --run` 与 `npm run typecheck`

失败处理：先定位并修复实现或测试，使其真实通过；禁止删除测试、`skip`、降低阈值。

最终出口：后端全量与前端全量通过；E.16.4 14 条验收全部通过；PRD 既有行为（敏感拒答、`q<60` confirm、改写、来源合并与 `[来源N]`）不回归。

## 5. 风险控制

- 外部网络测试全部使用 fake/mock；真实 Provider 仅在配置有 Key 时启用。
- 严格管道可能增加首字延迟，用 `status` 事件提示。
- 校验阈值取 E.14 默认值，先用规则引擎，不依赖 LLM 判定。
- 写回只针对严格管道实际引用且通过质量门的网页；通用流式不写回。

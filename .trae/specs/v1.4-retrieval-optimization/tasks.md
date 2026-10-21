# V1.4 任务清单

> 规格见 [spec.md](./spec.md)。按顺序执行，每阶段测试通过后再进入下一阶段。

## P0 基础防线
- [x] T0.1 `config.py` 增加 E.14 全部配置项
- [x] T0.2 新增 `core/evidence.py`（EvidencePack、HIT/PARTIAL/MISS/STALE、实体覆盖、时效、上下文预算）
- [x] T0.3 新增 `core/fallback_ladder.py`（L0~L4）
- [x] T0.4 新增 `core/input_guard.py`（空/超长/网页内容隔离/上下文守卫）
- [x] T0.5 `rag_service` 接入输入校验与上下文预算，行为不变
- [x] T0.6 新增 `test_evidence.py`、`test_fallback_ladder.py`、`test_input_guard.py`
- [x] T0.7 运行新增测试 + 现有全量测试

## P1 严格答案管道
- [x] T1.1 新增 `core/answer_verifier.py`（引用/事实/实体/冲突/退化/过度断言）
- [x] T1.2 新增 `services/answer_pipeline.py`（缓冲生成、修复 1 次、摘录兜底、通用流式守卫）
- [x] T1.3 新增 `test_answer_verifier.py`、`test_answer_pipeline.py`
- [x] T1.4 运行新增测试 + 全量回归

## P2 复杂度路由与混合分流
- [x] T2.1 新增 `core/complexity_router.py`（九类特征、三档、L2 下限、线上策略）
- [x] T2.2 `rag_service` 接入复杂度评分、`judge_answerability`、严格/通用/未找到分流
- [x] T2.3 新增 `test_complexity_router.py`、`test_hybrid_routing.py`
- [x] T2.4 运行新增测试 + 全量回归

## P3 线上检索与融合
- [x] T3.1 新增 `core/circuit_breaker.py`
- [x] T3.2 新增 `services/web_search.py`（Tavily/Bing/DDG/SearXNG、网关降级）
- [x] T3.3 新增 `services/web_fetcher.py`（SSRF、协议、超时、大小、并发）
- [x] T3.4 新增 `services/retrieval_orchestrator.py`（并行、融合重排、配额、连续编号）
- [x] T3.5 `state.py` / `rag_service.py` 接入（写回关闭）
- [x] T3.6 新增 `test_circuit_breaker.py`、`test_web_search.py`、`test_web_fetcher.py`、`test_retrieval_orchestrator.py`
- [x] T3.7 运行新增测试 + 全量回归

## P4 知识写回
- [x] T4.1 新增 `services/web_cache_store.py`
- [x] T4.2 新增 `services/web_writeback.py`（质量门、四层去重、TTL、落盘、入库、串行队列）
- [x] T4.3 `note_scanner` / `ingest_pipeline` 元数据透传与回调
- [x] T4.4 `metadata_store.get_document_by_source_url` / `vector_store.get_chunks_by_source_url`
- [x] T4.5 `state.py` / `rag_service.py` 接入写回队列
- [x] T4.6 新增 `test_web_cache_store.py`、`test_web_writeback.py`，增补既有存储/入库测试
- [x] T4.7 新增 `scripts/prune_web_cache.py`
- [x] T4.8 运行新增测试 + 全量回归

## P5 会话、SSE、前端、验收
- [x] T5.1 `session_store` 增 `answer_status`、`retrieval_traces`、历史过滤
- [x] T5.2 `rag_service` 增 request_id、status、meta/sources/done 字段、中断标记
- [x] T5.3 `schemas.py`、`routers/chat.py` 增加 web_mode/answer_mode 与错误协议
- [x] T5.4 前端类型、useChatStream、MessageBubble、SourceCard、CitationPanel、样式
- [x] T5.5 增补 `test_session_store.py`、新增 `test_rag_service_v14.py`、`test_acceptance_v14.py`
- [x] T5.6 前端新增/更新 vitest 用例
- [x] T5.7 后端全量 + 前端 test/typecheck 全部通过
- [x] T5.8 核对 E.16.4 14 条验收与既有行为不回归


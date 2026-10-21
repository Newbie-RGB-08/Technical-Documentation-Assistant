# V1.4 验收清单

> 每项必须有对应测试或运行证据。禁止删除测试、跳过测试或降低阈值。

## P0 基础防线
- [x] 证据快照编号连续、HIT/PARTIAL/MISS/STALE 判定符合 E.5
- [x] 兜底阶梯 L0~L4 文案与决策符合 E.8
- [x] 空问题/超长问题在检索前被拦截，网页内容有隔离标记与上下文预算
- [x] `test_evidence.py`、`test_fallback_ladder.py`、`test_input_guard.py` 通过

## P1 严格答案管道
- [x] 引用越界/缺失/关键实体/过度断言/冲突/退化均能识别
- [x] 修复仅一次，严重实体错误与退化直接摘录，模型异常走摘录兜底
- [x] `test_answer_verifier.py`、`test_answer_pipeline.py` 通过

## P2 复杂度路由与混合分流
- [x] simple/medium/complex 三档评分与线上策略符合 E.3
- [x] 有证据走严格管道；无证据宽泛概念走通用流式；具体问题明确未找到
- [x] `test_complexity_router.py`、`test_hybrid_routing.py` 通过

## P3 线上检索与融合
- [x] Provider 链降级、缓存、熔断、SSRF 防护、配额与连续编号有测试覆盖
- [x] `test_circuit_breaker.py`、`test_web_search.py`、`test_web_fetcher.py`、`test_retrieval_orchestrator.py` 通过

## P4 知识写回
- [x] 质量门、四层去重、TTL、front-matter、入库、后台串行队列均可用
- [x] `test_web_cache_store.py`、`test_web_writeback.py`、`test_metadata_store.py`、`test_ingest_pipeline.py` 通过
- [x] `scripts/prune_web_cache.py` 可执行且清理范围正确

## P5 会话 / SSE / 前端 / 验收
- [x] `messages.answer_status` 增量迁移、旧库兼容、仅 verified/extractive 回流
- [x] `retrieval_traces` 可写入与查询，中断标记 interrupted
- [x] SSE：status 事件、meta/sources/done 扩展字段、同会话并发锁、错误码结构
- [x] `test_session_store.py`、`test_rag_service_v14.py` 通过
- [x] `test_acceptance_v14.py` 覆盖 E.16.4 全部 14 条并通过
- [x] 前端 `npm test` 与 `npm run typecheck` 通过
- [x] 后端全量 pytest 通过
- [x] 既有行为不回归：敏感拒答、`q<60` confirm、多轮改写、来源合并、`[来源N]` 联动


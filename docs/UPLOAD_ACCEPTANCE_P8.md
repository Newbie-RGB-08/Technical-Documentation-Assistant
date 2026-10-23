# P8 加固与全量验收记录

实施阶段 P0~P8 已完成。本文件记录 P8 加固交付、AC1~AC11 验收证据与回退步骤。

日期：2026-10-23 · 后端 435 passed · 前端 98 passed（typecheck + build 全绿）

---

## 一、P8 加固交付

| 项 | 交付 | 验证 |
| --- | --- | --- |
| 删除/重试 | `delete / reparse / reindex` 走 `prepare_reparse`（清 index+parsed+assets 保留原文件）、`prepare_reindex`（auto_index）、`delete`（向量→元数据→资源→目录，`_refresh`） | 上传 API 测试 |
| 配额 | 单文件 100MB / 单批 20 / PDF 500 页 / 图片 300 / chunk 5000 / Markdown 500 万字符，全部在路由+流水线校验 | test_upload_api + pipeline |
| 超限行为 | Markdown→`IndexFailure`、chunk→`IndexFailure` 不写库、PDF 页数→解析前拒绝、文件过大/为空→拒收 | test_upload_api |
| 安全 | 路径穿越/ZIP 炸弹（zip_safe）/宏格式拒绝/超大文件拒绝+清半成品/DOMPurify 预览/`auto` 不选云 | test_ocr_adapters + 前端 |
| 可观测 | `/api/uploads/engines` 引擎健康缓存；上传列表含状态/错误/进度 | engine api |
| 指定引擎不可用 | `_check_engine` 用健康探测（非 available），不可达返回 503、不支持格式返回 422 | test_format_engine_and_empty_rejections |

### P8 修复的缺陷

- `test_format_engine_and_empty_rejections` 暴露：显式指定 mineru 引擎时，因 `available()` 仅检查 httpx 存在（恒 True），导致返回 422（格式错）而非 503（引擎不可达）。
  修复：`uploads._check_engine` 改为基于 `registry.health(engine)` 的真实健康判断，不可达→503、可达但格式不支持→422。

---

## 二、AC 验收证据

| 编号 | 验收项 | 证据 |
| --- | --- | --- |
| AC1 | 格式覆盖 | native parsers 覆盖 md/txt/pdf/docx/pptx/xlsx/csv/html；不支持格式明确提示（`test_upload_api` 400 分支） |
| AC2 | 引擎覆盖 | `PARSERS = native/rapidocr/mineru/paddlex`；`/api/uploads/engines` 返回；auto 降级（test_pdf_engine_choice） |
| AC3 | 文本抽取 | native `_parse_*` 保留标题/段落/代码；test_native_parsers |
| AC4 | 表格抽取 | DOCX/PPTX/XLSX→Markdown 表（native）；rich_chunker 表切分；P6/P7 覆盖 OCR 路径 |
| AC5 | 图片抽取 | native docx/pptx/xlsx `_images`→assets；MinerU ZIP `collect_images` 改链；删除清理 |
| AC6 | 切片与索引 | chunk 含 heading_path/content_type/file_path/upload_id（rich_chunker）；test_chunker/test_rich_chunker |
| AC7 | 状态与重试 | queued→…→indexed 状态机；failed* 可重试；启动恢复收敛（upload_pipeline + api） |
| AC8 | 非回归 | 后端 435 + 前端 98 + tsc + build 全绿；启动不因 OCR 依赖失败（延迟导入 + 惰性 health） |
| AC9 | 安全 | zip_safe 炸弹/穿越、宏格式、超大文件、DOMPurify、auto 不选云 |
| AC10 | 资源与恢复 | 加密/0 页 PDF 明确失败（native `_pdf_reader`）；单 worker；重启收敛 |
| AC11 | 来源隔离与迁移 | `origin=upload` 守卫；metadata_store/chroma 增量迁移（source_url/heading_path） |

### 命令与结果（全量）

```powershell
# 后端（含 Chroma/上传 API）
cd backend
$env:HF_HOME="D:\Program Files\Python Workspaces\Technical-Documentation-Assistant\.hf-cache"
..\.venv\Scripts\python.exe -m pytest tests/ -q    # 435 passed

# 前端
cd frontend
npm run test          # 98 passed
npx tsc --noEmit      # 0 error
npm run build         # built in ~1.3s

# Compose
docker compose -f deploy/parsers/docker-compose.yml --profile mineru --profile paddlex config   # OK
```

---

## 三、回退步骤

遇到回归问题时按依赖序回退：

1. **回退到纯本地**：设置 `UPLOAD_ENABLED=false`（后端 .env），停用 Composer（`docker compose --profile mineru --profile paddlex down`），仅保留 native + 对话。RapidOCR/MinerU/PaddleX 均不可达不影响对话功能。
2. **回退解析行为**：`.env` 里 `UPLOAD_PDF_TEXT_MIN_CHARS=0` 强制所有 PDF 走 native；`UPLOAD_IMAGE_OCR=false` 关闭图片 OCR。
3. **回退提交**：删除 `deploy/parsers/` 目录即可解除 OCR 容器依赖；后端仍可启动（解析器延迟导入）。

> 说明：native 对话（RAG 检索/回答）与上传功能相互独立；删除 `deploy/parsers` 不会破坏对话主流程。

---

## 四、遗留说明（非 P0/P1）

- MinerU/PaddleX 容器冒烟需拉镜像与模型（`docker compose up` 实测），本机未长效拉起；Compose 语法与 health URL 已校验，`pytest.mark.integration` 默认不依赖容器。
- crawl4ai 网页抓取依赖权限问题未验收（不影响上传）。
- 上传依赖 MySQL（`rag_meta`）；未启用 MySQL 时上传不可用，但对话不受影响。
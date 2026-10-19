"""文档管理接口（PRD 3.5.2）：异步入库 + 任务状态。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.models.schemas import IngestRequest, IngestResponse
from app.services.ingest_pipeline import ingest_directory

router = APIRouter(prefix="/api/documents")


def _run_ingest(app_state, directory: str, source_type: str, force: bool, task_id: str) -> None:
    """同步函数，BackgroundTasks 在线程池中执行。"""
    try:
        app_state.metadata.update_task(task_id, status="processing")
        summary = ingest_directory(
            directory,
            source_type=source_type,
            vector_store=app_state.chroma,
            metadata_store=app_state.metadata,
            force_reingest=force,
        )
        bm25 = getattr(app_state, "bm25", None)
        if bm25 is not None:
            bm25.load_from_chroma(app_state.chroma)  # BM25 语料刷新
        # 检索评分关键字权重随知识库内容刷新，否则新入库文档不参与定级
        from app.state import build_retrieval_scorer

        app_state.retrieval_scorer = build_retrieval_scorer(app_state.metadata)
        status = "completed" if summary["failed"] == 0 else "failed"
        app_state.metadata.update_task(
            task_id,
            status=status,
            chunk_count=summary["total_chunks"],
            error_message=str(summary["errors"]) if summary["errors"] else None,
        )
    except Exception as exc:  # noqa: BLE001
        app_state.metadata.update_task(task_id, status="failed", error_message=str(exc))


@router.post("/ingest", response_model=IngestResponse)
async def ingest_documents(req: IngestRequest, bg: BackgroundTasks, request: Request):
    if req.source_type not in ("tech_doc", "personal_note"):
        raise HTTPException(status_code=400, detail="source_type 必须为 tech_doc 或 personal_note")

    app_state = request.app.state.core
    files = app_state.metadata.list_documents()  # 触发连接校验
    del files

    from pathlib import Path

    directory = Path(req.directory)
    if not directory.is_absolute():
        directory = Path(__file__).resolve().parent.parent.parent / req.directory
    file_count = len(list(directory.glob("**/*.md"))) if directory.exists() else 0

    task_id = str(uuid.uuid4())
    app_state.metadata.create_task(task_id, file_count=file_count)
    bg.add_task(_run_ingest, app_state, str(directory), req.source_type, req.force_reingest, task_id)
    return IngestResponse(task_id=task_id, status="processing", file_count=file_count)


@router.get("/status/{task_id}")
async def get_ingest_status(task_id: str, request: Request):
    task = request.app.state.core.metadata.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/list")
async def list_documents(request: Request, source_type: str | None = None):
    return request.app.state.core.metadata.list_documents(source_type=source_type)

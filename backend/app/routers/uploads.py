"""知识库上传接口（PRD 7.9）。"""
from __future__ import annotations

import re
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from app.config import get_settings
from app.parsers.base import UnsupportedFormatError
from app.parsers.native import IMAGE_EXTENSIONS, NativeParser, validate_extension
from app.parsers.registry import PARSERS, get_registry
from app.services.asset_store import (
    EmptyFileError,
    FileTooLargeError,
    UploadStorageError,
)
from app.services.upload_pipeline import ParseFailure, UploadPipelineError

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

_ENGINES = ("auto", "native", "rapidocr", "mineru", "paddlex")
_NATIVE_EXTENSIONS = set(NativeParser.supported_extensions)
_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "txt": "text/plain",
    "html": "text/html",
    "htm": "text/html",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv",
    "json": "application/json",
}


def _new_id(prefix: str, exists) -> str:
    for _ in range(10):
        candidate = f"{prefix}_{datetime.now():%Y%m%d}_{secrets.token_hex(4)}"
        if not exists(candidate):
            return candidate
    raise RuntimeError("无法生成唯一 ID")


def _core(request: Request):
    return request.app.state.core


def _pipeline(request: Request):
    pipeline = getattr(_core(request), "upload_pipeline", None)
    if pipeline is None:
        raise HTTPException(status_code=503, detail="上传功能未初始化")
    return pipeline


def _upload_store(request: Request):
    store = getattr(_core(request), "upload_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="上传功能未初始化")
    return store


def _ensure_ready(request: Request) -> None:
    settings = get_settings()
    core = _core(request)
    if not settings.upload_enabled:
        raise HTTPException(status_code=503, detail="上传功能已关闭")
    metadata = core.metadata
    if not getattr(metadata, "source_type_enum_ready", False):
        raise HTTPException(
            status_code=503,
            detail="数据库未就绪：请执行 documents.source_type 枚举迁移",
        )


def _check_engine(engine: str, ext: str, filename: str) -> str:
    """校验指定引擎与格式；auto 在图片无 OCR 时明确拒绝。"""
    registry = get_registry()
    if engine == "auto":
        if ext == ".pdf":
            # PDF 由流水线按文本层决定 native 或 OCR；无 OCR 时仍可处理数字 PDF。
            return "auto"
        if ext in _NATIVE_EXTENSIONS:
            return "native"
        if ext in IMAGE_EXTENSIONS:
            if registry.available("rapidocr"):
                return "rapidocr"
            raise HTTPException(
                status_code=422,
                detail=f"当前没有可用的图片 OCR 引擎，无法解析 {filename}",
            )
        raise HTTPException(
            status_code=422,
            detail=f"引擎不支持 {ext}；native 支持: {', '.join(sorted(_NATIVE_EXTENSIONS))}",
        )
    if engine == "native":
        if ext in _NATIVE_EXTENSIONS:
            return "native"
        raise HTTPException(
            status_code=422,
            detail=f"native 不支持 {ext}；支持: {', '.join(sorted(_NATIVE_EXTENSIONS))}",
        )
    if engine not in PARSERS:
        raise HTTPException(status_code=400, detail=f"未知解析引擎: {engine}")
    health = registry.health(engine)
    if not health.get("available"):
        raise HTTPException(
            status_code=503, detail=f"{engine} 不可达；请改用 auto 或启动对应引擎"
        )
    formats = set(health.get("formats") or [])
    if formats and ext not in formats:
        raise HTTPException(status_code=422, detail=f"{engine} 不支持 {ext}")
    return engine


def _rewrite_markdown(markdown: str, upload_id: str) -> str:
    prefix = f"/api/uploads/{upload_id}/"
    markdown = re.sub(
        r"\]\(assets/([^)]+)\)",
        lambda m: f"]({prefix}assets/{m.group(1)})",
        markdown,
    )
    markdown = re.sub(
        r'(src=["\'])assets/([^"\']+)',
        lambda m: f"{m.group(1)}{prefix}assets/{m.group(2)}",
        markdown,
    )
    return markdown


def _rejected(filename: str, ext: str, size: int | None, status: str, message: str) -> dict:
    return {
        "filename": filename,
        "format": ext.lstrip("."),
        "size": size,
        "status": status,
        "error": message,
    }


def _all_rejected_response(rejected: list[dict]):
    statuses = {item["status"] for item in rejected}
    if statuses == {"duplicate"}:
        code = 409
        message = rejected[0].get("error") or "已存在相同内容"
    elif "oversize" in statuses:
        code = 413
        message = "文件超过大小上限"
    elif "engine_unavailable" in statuses:
        code = 503
        message = rejected[0].get("error") or "指定引擎不可用"
    elif "engine_mismatch" in statuses:
        code = 422
        message = rejected[0].get("error") or "引擎不支持该格式"
    else:
        code = 400
        message = rejected[0].get("error") or "没有可上传的文件"
    return JSONResponse(status_code=code, content={"detail": message, "files": rejected})


# 注意：所有静态路由必须注册在 /{upload_id} 之前。
@router.get("/engines")
async def list_engines(request: Request):
    _ = request
    return {"engines": get_registry().capabilities()}


@router.get("/tasks/{task_id}")
async def get_upload_task(task_id: str, request: Request):
    store = _upload_store(request)
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {**task, "files": store.files_for_task(task_id)}


@router.get("")
async def list_uploads(
    request: Request,
    status: str | None = None,
    format: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    _ensure_ready(request)
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    items, total = _upload_store(request).list_files(
        status=status,
        doc_format=format,
        query=q,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("", status_code=202)
async def create_upload(
    request: Request,
    files: list[UploadFile] | None = File(default=None),
    engine: str = Form("auto"),
    auto_index: bool = Form(True),
    chunk_size: int = Form(512),
    chunk_overlap: int = Form(64),
    tags: str = Form(""),
    force: bool = Form(False),
):
    _ensure_ready(request)
    settings = get_settings()
    core = _core(request)
    pipeline = _pipeline(request)
    store = _upload_store(request)
    asset_store = getattr(core, "asset_store", None)
    if asset_store is None:
        raise HTTPException(status_code=503, detail="上传存储未初始化")

    engine = (engine or "auto").strip().lower()
    if engine not in _ENGINES:
        raise HTTPException(status_code=400, detail=f"未知解析引擎: {engine}")
    files = files or []
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")
    if len(files) > settings.upload_max_files_per_batch:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多上传 {settings.upload_max_files_per_batch} 个文件",
        )
    if chunk_size < 64 or chunk_size > 4096:
        raise HTTPException(status_code=400, detail="chunk_size 必须在 64~4096 之间")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise HTTPException(status_code=400, detail="chunk_overlap 必须小于 chunk_size")
    _ = tags  # 当前表结构未持久化标签；保留表单兼容，不阻断上传。

    accepted: list[dict] = []
    rejected: list[dict] = []
    seen_hashes: dict[str, str] = {}
    task_id = _new_id("t", lambda candidate: store.get_task(candidate) is not None)
    max_bytes = settings.upload_max_mb * 1024 * 1024

    for upload in files:
        filename = upload.filename or "upload.bin"
        ext = ""
        size = None
        try:
            ext = validate_extension(filename)
            actual_engine = _check_engine(engine, ext, filename)
            upload_id = _new_id("u", lambda candidate: store.get_file(candidate) is not None)
            await upload.seek(0)
            saved = asset_store.save_stream(
                upload_id, filename, upload.file, max_bytes=max_bytes
            )
            size = saved["file_size"]
            digest = saved["content_hash"]
            if digest in seen_hashes:
                asset_store.delete_upload(upload_id)
                rejected.append(
                    _rejected(
                        filename,
                        ext,
                        size,
                        "duplicate",
                        f"本批次已有相同内容（upload_id={seen_hashes[digest]}）",
                    )
                )
                continue
            existing = None if force else store.find_by_hash(digest)
            if existing is not None:
                asset_store.delete_upload(upload_id)
                rejected.append(
                    _rejected(
                        filename,
                        ext,
                        size,
                        "duplicate",
                        f"已存在相同内容（upload_id={existing['upload_id']}）",
                    )
                )
                continue
            seen_hashes[digest] = upload_id
            accepted.append(
                {
                    "upload_id": upload_id,
                    "filename": filename,
                    "ext": ext,
                    "format": ext.lstrip("."),
                    "size": size,
                    "content_hash": digest,
                    "stored_path": saved["stored_path"],
                    "engine": actual_engine,
                }
            )
        except UnsupportedFormatError as exc:
            rejected.append(_rejected(filename, ext, size, "rejected", str(exc)))
        except FileTooLargeError as exc:
            rejected.append(_rejected(filename, ext, size, "oversize", str(exc)))
        except EmptyFileError as exc:
            rejected.append(_rejected(filename, ext, size, "rejected", str(exc)))
        except UploadStorageError as exc:
            rejected.append(_rejected(filename, ext, size, "rejected", str(exc)))
        except HTTPException as exc:
            status = "engine_unavailable" if exc.status_code == 503 else "engine_mismatch"
            rejected.append(_rejected(filename, ext, size, status, str(exc.detail)))
        except OSError as exc:
            rejected.append(
                _rejected(filename, ext, size, "rejected", f"文件落盘失败: {exc}")
            )

    if not accepted:
        return _all_rejected_response(rejected)

    store.create_task(
        task_id,
        engine=engine,
        total_files=len(accepted),
        status="queued",
    )
    created_ids: list[str] = []
    try:
        for item in accepted:
            store.create_file(
                item["upload_id"],
                item["filename"],
                item["stored_path"],
                item["format"],
                item["size"],
                item["content_hash"],
                task_id=task_id,
                auto_index=auto_index,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                status="queued",
            )
            created_ids.append(item["upload_id"])
    except Exception as exc:  # noqa: BLE001 - 建记录失败必须清理已落盘文件
        for upload_id in created_ids:
            store.delete_file(upload_id)
        for item in accepted:
            try:
                asset_store.delete_upload(item["upload_id"])
            except Exception:  # noqa: BLE001
                pass
        store.update_task(task_id, status="failed", error_message=str(exc))
        raise HTTPException(status_code=500, detail=f"上传记录创建失败: {exc}") from exc

    try:
        pipeline.submit(task_id)
    except Exception as exc:  # noqa: BLE001
        store.update_task(task_id, status="failed", error_message=str(exc))
        raise HTTPException(status_code=503, detail=f"无法提交解析任务: {exc}") from exc

    response_files = [
        {
            "upload_id": item["upload_id"],
            "filename": item["filename"],
            "format": item["format"],
            "size": item["size"],
            "status": "queued",
        }
        for item in accepted
    ] + rejected
    return {"task_id": task_id, "files": response_files}


@router.get("/{upload_id}/markdown")
async def get_upload_markdown(upload_id: str, request: Request):
    store = _upload_store(request)
    record = store.get_file(upload_id)
    if not record:
        raise HTTPException(status_code=404, detail="上传记录不存在")
    asset_store = getattr(_core(request), "asset_store", None)
    if asset_store is None:
        raise HTTPException(status_code=503, detail="上传存储未初始化")
    try:
        path = asset_store.markdown_path(upload_id)
    except UploadStorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Markdown 尚未生成")
    text = _rewrite_markdown(path.read_text(encoding="utf-8"), upload_id)
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


@router.get("/{upload_id}/original")
async def get_upload_original(upload_id: str, request: Request):
    store = _upload_store(request)
    record = store.get_file(upload_id)
    if not record:
        raise HTTPException(status_code=404, detail="上传记录不存在")
    asset_store = getattr(_core(request), "asset_store", None)
    if asset_store is None:
        raise HTTPException(status_code=503, detail="上传存储未初始化")
    try:
        path = asset_store.original_path(upload_id, record.get("stored_path"))
    except UploadStorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="原文件不存在")
    media_type = _MEDIA_TYPES.get(record["doc_format"], "application/octet-stream")
    return FileResponse(
        path,
        media_type=media_type,
        filename=record["original_filename"],
        content_disposition_type="inline",
    )


@router.get("/{upload_id}/assets/{name:path}")
async def get_upload_asset(upload_id: str, name: str, request: Request):
    store = _upload_store(request)
    if not store.get_file(upload_id):
        raise HTTPException(status_code=404, detail="上传记录不存在")
    asset_store = getattr(_core(request), "asset_store", None)
    if asset_store is None:
        raise HTTPException(status_code=503, detail="上传存储未初始化")
    try:
        path = asset_store.asset_path(upload_id, name)
    except UploadStorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="资源不存在")
    return FileResponse(path)


@router.post("/{upload_id}/reparse", status_code=202)
async def reparse_upload(upload_id: str, request: Request):
    _ensure_ready(request)
    pipeline = _pipeline(request)
    store = _upload_store(request)
    record = store.get_file(upload_id)
    if not record:
        raise HTTPException(status_code=404, detail="上传记录不存在")
    if record["status"] in ("parsing", "indexing"):
        raise HTTPException(status_code=409, detail="文件正在处理中，请稍后再试")
    old_task = store.get_task(record["task_id"]) if record.get("task_id") else None
    task_id = _new_id("t", lambda candidate: store.get_task(candidate) is not None)
    store.create_task(
        task_id,
        engine=(old_task or {}).get("engine", "auto"),
        total_files=1,
        status="queued",
    )
    try:
        pipeline.prepare_reparse(upload_id, task_id)
        pipeline.submit(task_id)
    except UploadPipelineError as exc:
        store.update_task(task_id, status="failed", error_message=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"task_id": task_id, "status": "queued"}


@router.post("/{upload_id}/reindex", status_code=202)
async def reindex_upload(upload_id: str, request: Request):
    _ensure_ready(request)
    pipeline = _pipeline(request)
    store = _upload_store(request)
    record = store.get_file(upload_id)
    if not record:
        raise HTTPException(status_code=404, detail="上传记录不存在")
    if record["status"] in ("parsing", "indexing"):
        raise HTTPException(status_code=409, detail="文件正在处理中，请稍后再试")
    old_task = store.get_task(record["task_id"]) if record.get("task_id") else None
    task_id = _new_id("t", lambda candidate: store.get_task(candidate) is not None)
    store.create_task(
        task_id,
        engine=(old_task or {}).get("engine", "auto"),
        total_files=1,
        status="queued",
    )
    try:
        pipeline.prepare_reindex(upload_id, task_id)
        pipeline.submit(task_id)
    except UploadPipelineError as exc:
        store.update_task(task_id, status="failed", error_message=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"task_id": task_id, "status": "queued"}


@router.delete("/{upload_id}")
async def delete_upload(upload_id: str, request: Request):
    _ensure_ready(request)
    pipeline = _pipeline(request)
    store = _upload_store(request)
    record = store.get_file(upload_id)
    if not record:
        raise HTTPException(status_code=404, detail="上传记录不存在")
    if record["status"] in ("parsing", "indexing"):
        raise HTTPException(status_code=409, detail="文件正在处理中，请稍后再试")
    removed = pipeline.delete(upload_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="上传记录不存在")
    return {"ok": True, "upload_id": upload_id}


@router.get("/{upload_id}")
async def get_upload(upload_id: str, request: Request):
    store = _upload_store(request)
    record = store.get_file(upload_id)
    if not record:
        raise HTTPException(status_code=404, detail="上传记录不存在")
    return {**record, "assets": store.list_assets(upload_id)}

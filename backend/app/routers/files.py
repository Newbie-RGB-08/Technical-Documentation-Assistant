"""本地文件打开接口（V1.1 新增，附录 B.3.3）。

前端点击"本地笔记"类来源卡片时，调此接口让后端用默认应用打开 .md。
安全约束：只允许打开 backend/data/{notes,tech_docs}/ 下的 .md 文件。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api", tags=["files"])

# 允许打开的根目录（相对于 backend/），白名单校验在 _assert_allowed 里做
_ALLOWED_ROOTS = ("data/notes", "data/tech_docs")


def _allowed_roots() -> tuple[Path, ...]:
    """返回允许打开的绝对根目录列表。

    本文件位于 backend/app/routers/files.py，因此 backend/ 是 parents[2]。
    注意不能写成 parent.parent（那只退到 backend/app，会把白名单错算成
    backend/app/data/，导致真实笔记一律 403）。
    抽成函数便于测试 monkeypatch，避免夹具与实现共用同一套推导形成循环验证。
    """
    backend_root = Path(__file__).resolve().parents[2]
    return tuple(backend_root / r for r in _ALLOWED_ROOTS)


def _assert_allowed(path_str: str) -> Path:
    """把传入路径解析为绝对 Path，并校验是否落在允许的根目录下。"""
    try:
        p = Path(path_str).resolve()
        if not p.exists():
            raise HTTPException(404, f"文件不存在: {path_str}")
        if p.suffix.lower() != ".md":
            raise HTTPException(400, "只允许打开 .md 文件")

        roots = _allowed_roots()
        if not any(p.is_relative_to(root) for root in roots):
            raise HTTPException(
                403,
                f"路径不在允许范围（允许: {', '.join(_ALLOWED_ROOTS)}）",
            )
    except HTTPException:
        raise
    except (PermissionError, OSError) as exc:
        # resolve / exists / stat 阶段被系统拒绝（比如 SAM 文件）
        raise HTTPException(403, f"路径访问被拒绝: {exc}")
    return p


@router.get("/files/open")
async def open_local_file(path: str = Query(..., description="md 文件绝对路径")):
    p = _assert_allowed(path)
    try:
        if os.name == "nt":
            os.startfile(str(p))  # type: ignore[attr-defined]
        else:  # pragma: no cover - 开发主要在 Windows
            import webbrowser

            webbrowser.open(p.as_uri())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"打开失败: {exc}")
    return {"ok": True, "path": str(p)}

"""V1.1 新增：/api/files/open 接口的安全与行为测试。

夹具不采用"照实现再推导一遍根目录"的写法（那会形成循环验证，
历史上正是它掩盖了 backend_root 少退一级、真实笔记一律 403 的 bug）。
统一 monkeypatch `_allowed_roots`，显式给出临时白名单目录。
"""
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.routers import files as files_module
from app.routers.files import _allowed_roots, _assert_allowed


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(files_module.router)
    return TestClient(app)


@pytest.fixture
def sandbox(tmp_path):
    """构造 backend/data/{notes,tech_docs} 结构，并把它设为白名单根。"""
    notes = tmp_path / "backend" / "data" / "notes"
    tech = tmp_path / "backend" / "data" / "tech_docs"
    notes.mkdir(parents=True)
    tech.mkdir(parents=True)
    with patch.object(files_module, "_allowed_roots", return_value=(notes, tech)):
        yield {"notes": notes, "tech": tech, "tmp": tmp_path}


# ---- 回归：白名单根必须落在 <repo>/backend/data/ 下（曾错成 backend/app/data）----

def test_allowed_roots_point_to_backend_data_dir():
    roots = _allowed_roots()
    assert roots, "白名单根不应为空"
    for root in roots:
        assert root.parent.name == "data", f"{root} 的父目录应为 data"
        assert root.parent.parent.name == "backend", f"{root} 应位于 backend/data 下"
    assert {r.name for r in roots} == {"notes", "tech_docs"}


def test_real_note_path_is_accepted():
    """真实笔记路径必须可打开（防止再次退错层级导致 403）。"""
    notes = _allowed_roots()[0].parent / "notes"
    md_files = sorted(notes.glob("*.md"))
    if not md_files:
        pytest.skip("当前无真实笔记文件可用于校验")
    assert _assert_allowed(str(md_files[0])) == md_files[0].resolve()


# ---- _assert_allowed 单元测试 ----

def test_assert_allowed_accepts_whitelisted_md(sandbox):
    p = sandbox["notes"] / "demo.md"
    p.write_text("# hi", encoding="utf-8")
    assert _assert_allowed(str(p)) == p.resolve()


def test_assert_allowed_accepts_tech_docs_root(sandbox):
    p = sandbox["tech"] / "crawl.md"
    p.write_text("# crawl", encoding="utf-8")
    assert _assert_allowed(str(p)) == p.resolve()


def test_assert_allowed_rejects_non_md(sandbox):
    p = sandbox["notes"] / "evil.txt"
    p.write_text("txt", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        _assert_allowed(str(p))
    assert exc.value.status_code == 400


def test_assert_allowed_404_when_missing():
    with pytest.raises(HTTPException) as exc:
        _assert_allowed("D:/nonexistent_file_xyz.md")
    assert exc.value.status_code == 404


def test_assert_allowed_403_outside_whitelist(sandbox):
    """真实存在但不在白名单根内 → 403。"""
    evil = sandbox["tmp"] / "backend" / "private" / "secret.md"
    evil.parent.mkdir(parents=True)
    evil.write_text("secret", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        _assert_allowed(str(evil))
    assert exc.value.status_code == 403


def test_assert_allowed_403_on_path_traversal(sandbox):
    """`..` 穿越：resolve 归一后仍判定，逃不出去。"""
    escape = sandbox["notes"] / ".." / ".." / ".." / "outside.md"
    escape.parent.mkdir(parents=True, exist_ok=True)
    escape.write_text("escaped", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        _assert_allowed(str(escape))
    assert exc.value.status_code == 403


def test_assert_allowed_blocks_windows_system_path():
    """验收 B.6#4：系统敏感路径被拦截（400/403/404 任一拦截码都算）。"""
    for evil in (
        r"C:\Windows\System32\config\SAM",
        r"C:\Windows\System32\drivers\etc\hosts",
    ):
        try:
            _assert_allowed(evil)
            pytest.fail(f"{evil} 应被拦截")
        except HTTPException as exc:
            assert exc.status_code in (400, 403, 404)
        except (PermissionError, OSError):
            pass  # resolve/exists 阶段被系统直接拒绝，等价于拦截


# ---- 接口层 ----

def test_open_happy_path_returns_200(client, sandbox):
    p = sandbox["notes"] / "open_test.md"
    p.write_text("# open", encoding="utf-8")
    with patch("os.startfile", return_value=None):
        resp = client.get("/api/files/open", params={"path": str(p)})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "path": str(p.resolve())}


def test_open_propagates_403(client, sandbox):
    evil = sandbox["tmp"] / "outside.md"
    evil.write_text("no", encoding="utf-8")
    resp = client.get("/api/files/open", params={"path": str(evil)})
    assert resp.status_code == 403


def test_open_propagates_404(client):
    resp = client.get(
        "/api/files/open",
        params={"path": "D:/definitely_not_here_1234567890.md"},
    )
    assert resp.status_code == 404


def test_open_propagates_400_non_md(client, sandbox):
    bad = sandbox["notes"] / "file.txt"
    bad.write_text("x", encoding="utf-8")
    resp = client.get("/api/files/open", params={"path": str(bad)})
    assert resp.status_code == 400

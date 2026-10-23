"""开发启动脚本一致性守卫。

校验 `run-dev.ps1`：
1. PowerShell 语法可解析（不执行，避免起服务）；
2. 默认端口与 vite 代理默认目标一致（防止"后端换了端口、前端仍指 8000"）；
3. 统一设置 HF_HOME 与 VITE_PROXY_TARGET。

校验 `start-backend.bat` / `start-frontend.bat`（Windows GBK 编码）：
4. 后端启动前必须按 `route_prefixes` 判断是否加载了 /api/uploads（防旧进程误判）；
5. 前端必须支持 VITE_PROXY_TARGET 覆盖，且不再展示写死的 localhost:8000。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "run-dev.ps1"
PROXY_TS = PROJECT_ROOT / "frontend" / "config" / "proxy.ts"
BACKEND_BAT = PROJECT_ROOT / "start-backend.bat"
FRONTEND_BAT = PROJECT_ROOT / "start-frontend.bat"

# cmd.exe 在中文 Windows 下按 GBK 解析批处理，写成 UTF-8 会导致中文串错位
BAT_ENCODING = "gbk"


def test_script_exists():
    assert SCRIPT.is_file(), f"缺少启动脚本: {SCRIPT}"


def test_script_powershell_syntax_valid(tmp_path):
    checker = tmp_path / "check_syntax.ps1"
    checker.write_text(
        "param([string]$Path)\n"
        "$tokens = $null\n"
        "$errors = $null\n"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$Path, [ref]$tokens, [ref]$errors) | Out-Null\n"
        "if ($errors.Count -gt 0) {\n"
        "  $errors | ForEach-Object { Write-Output $_.Message }\n"
        "  exit 1\n"
        "}\n"
        "Write-Output 'SYNTAX_OK'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(checker), str(SCRIPT),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SYNTAX_OK" in result.stdout


def test_default_port_matches_vite_proxy_target():
    script = SCRIPT.read_text(encoding="utf-8")
    proxy_ts = PROXY_TS.read_text(encoding="utf-8")

    script_port = int(re.search(r"\[int\]\$BackendPort\s*=\s*(\d+)", script).group(1))
    proxy_port = int(re.search(r'127\.0\.0\.1:(\d+)', proxy_ts).group(1))
    assert script_port == proxy_port == 8000


def test_script_sets_required_env():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "HF_HOME" in script
    assert "VITE_PROXY_TARGET" in script
    assert '"http://127.0.0.1:$BackendPort"' in script
    assert "uvicorn" in script and "app.main:app" in script
    assert "npm run dev" in script


@pytest.mark.parametrize("flag", ["-Reload", "-NoFrontend", "-BackendPort", "-FrontendPort"])
def test_script_exposes_switches(flag):
    assert flag in SCRIPT.read_text(encoding="utf-8")


# ---------------- start-backend.bat / start-frontend.bat ----------------

def _read_bat(path: Path) -> str:
    raw = path.read_bytes()
    text = raw.decode(BAT_ENCODING)
    assert "\ufffd" not in text, f"{path.name} 含无法解码的字节"
    return text


@pytest.mark.parametrize("path", [BACKEND_BAT, FRONTEND_BAT])
def test_bat_exists_and_is_gbk(path):
    assert path.is_file(), f"缺少启动脚本: {path}"
    text = _read_bat(path)
    assert text.strip().startswith("@echo off")
    assert "PTDRA" in text


@pytest.mark.parametrize("path", [BACKEND_BAT, FRONTEND_BAT])
def test_bat_requires_crlf_and_fixed_codepage(path):
    """回归：LF 换行会让 cmd 逐字节解析错位，出现 'xist'/'--no-audit' 之类的报错。"""
    raw = path.read_bytes()
    assert raw.count(b"\r\n") > 10
    assert raw.count(b"\n") == raw.count(b"\r\n"), "批处理必须使用 CRLF 换行"
    assert raw.count(b"\r") == raw.count(b"\r\n"), "存在孤立的 CR（可能是 \\r\\r\\n）"
    assert "chcp 936 >nul" in _read_bat(path)


@pytest.mark.parametrize("path", [BACKEND_BAT, FRONTEND_BAT])
def test_bat_echo_lines_escape_special_chars(path):
    """回归：echo 行里的 > | & 必须用 ^ 转义，否则 cmd 会当成重定向/管道并报错。"""
    for line in _read_bat(path).splitlines():
        if not line.lstrip().lower().startswith("echo"):
            continue
        probe = (
            line.replace("^>", "").replace("^|", "").replace("^&", "").replace("^^", "")
        )
        assert not re.search(r"[>|&]", probe), f"未转义的批处理控制符: {line}"


def test_backend_bat_rejects_stale_process_without_uploads_route():
    """回归：旧进程（/api/health 正常但无 /api/uploads）不得被当作"已在运行"。"""
    text = _read_bat(BACKEND_BAT)
    assert "route_prefixes -contains '/api/uploads'" in text
    assert ":stale_running" in text and ":already_running" in text
    assert '"%errorlevel%"=="11"' in text
    # 拦截到旧进程时给出排查指引，而不是直接打开文档
    assert "旧版进程" in text and "netstat -ano" in text


def test_backend_bat_pins_hf_home_to_project():
    """模型缓存固定在项目内，与 run-dev.ps1 一致，避免散落到用户目录。"""
    text = _read_bat(BACKEND_BAT)
    assert 'set "HF_HOME=%ROOT%.hf-cache"' in text
    assert "'-m','uvicorn','app.main:app'" in text
    assert '127.0.0.1' in text and '"PORT=8000"' in text


def test_frontend_bat_uses_configurable_proxy_target():
    text = _read_bat(FRONTEND_BAT)
    assert "if not defined VITE_PROXY_TARGET set" in text
    assert "%VITE_PROXY_TARGET%" in text
    # 不再出现写死的 localhost:8000 提示（改为 127.0.0.1 且可覆盖）
    assert "localhost:8000" not in text
    assert "npm_config_cache=%ROOT%.npm-cache" in text


def test_frontend_bat_default_proxy_port_matches_vite_default():
    text = _read_bat(FRONTEND_BAT)
    proxy_ts = PROXY_TS.read_text(encoding="utf-8")
    bat_port = int(re.search(r"VITE_PROXY_TARGET=http://127\.0\.0\.1:(\d+)", text).group(1))
    proxy_port = int(re.search(r'127\.0\.0\.1:(\d+)', proxy_ts).group(1))
    assert bat_port == proxy_port == 8000


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))

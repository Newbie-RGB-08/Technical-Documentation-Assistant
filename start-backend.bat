@echo off
setlocal
chcp 936 >nul
title PTDRA 后端服务

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "PY=%ROOT%.venv\Scripts\python.exe"
set "PORT=8000"
set "URL=http://127.0.0.1:%PORT%"
set "DOCS=%URL%/docs"

echo ============================================
echo    PTDRA 后端服务（FastAPI）
echo    目录: %BACKEND%
echo    地址: %DOCS%
echo ============================================
echo.

if not exist "%PY%" (
    echo [错误] 未找到虚拟环境解释器:
    echo        %PY%
    echo    请先创建虚拟环境并安装依赖:
    echo        python -m venv "%ROOT%.venv"
    echo        "%ROOT%.venv\Scripts\python.exe" -m pip install -r "%BACKEND%\requirements.txt"
    echo.
    pause
    exit /b 1
)

if not exist "%BACKEND%\app\main.py" (
    echo [错误] 未找到后端入口: %BACKEND%\app\main.py
    echo.
    pause
    exit /b 1
)

rem ---------- 模型缓存固定在项目内，避免散落到用户目录 ----------
set "HF_HOME=%ROOT%.hf-cache"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem ---------- 端口探测：健康检查必须包含 /api/uploads 路由，才算"新版后端已在运行" ----------
powershell -NoProfile -ExecutionPolicy Bypass -Command "try{ $h=Invoke-RestMethod -Uri '%URL%/api/health' -TimeoutSec 3 -ErrorAction Stop; if($h.route_prefixes -contains '/api/uploads'){ exit 10 } else { exit 11 } }catch{ exit 0 }"
if "%errorlevel%"=="10" goto already_running
if "%errorlevel%"=="11" goto stale_running

echo [1/3] 正在后台启动 uvicorn（日志输出到新窗口）...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PY%' -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','%PORT%') -WorkingDirectory '%BACKEND%'"

echo [2/3] 等待后端就绪并确认已加载新路由（首次会探测本地模型）...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='%URL%/api/health'; for($i=0;$i -lt 150;$i++){ try{ $h=Invoke-RestMethod -Uri $u -TimeoutSec 3 -ErrorAction Stop; if($h.route_prefixes -contains '/api/uploads'){ exit 10 } else { exit 11 } }catch{}; Start-Sleep -Seconds 2 }; exit 1"
if "%errorlevel%"=="11" goto stale_running
if "%errorlevel%"=="1" goto not_ready

echo [3/3] 后端已就绪，正在打开接口文档...
start "" "%DOCS%"
goto finished

:already_running
echo [提示] 检测到 %PORT% 端口上的新版后端已在运行，直接打开接口文档。
echo.
start "" "%DOCS%"
goto finished

:stale_running
echo [警告] %PORT% 端口上的后端未注册 /api/uploads，说明是旧版进程（启动于代码更新之前）。
echo        请关闭旧的 python 日志窗口后重新运行本脚本。
echo        查看占用进程: netstat -ano ^| findstr :%PORT%
echo.
start "" "%DOCS%"
goto finished

:not_ready
echo [错误] 等待超时：后端未能注册 /api/uploads 路由，请查看 python 日志窗口。
echo        常见原因：端口被旧进程占用，或启动时报错。
echo        手动检查: %URL%/api/health
goto finished

:finished
echo.
echo --------------------------------------------
echo 常用地址:
echo   接口文档  %DOCS%
echo   健康检查  %URL%/api/health（含 route_prefixes / route_count / started_at）
echo   模型列表  %URL%/api/models
echo.
echo 关闭服务: 关闭标题为 "python" 的日志窗口即可。
echo 同时启动前后端: run-dev.ps1（推荐）
echo --------------------------------------------
powershell -NoProfile -Command "Start-Sleep -Seconds 8"
endlocal

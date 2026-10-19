@echo off
setlocal
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

rem ---------- 端口已被占用时直接复用，不重复启动 ----------
powershell -NoProfile -ExecutionPolicy Bypass -Command "try{ $r=Invoke-WebRequest -Uri '%URL%/api/health' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){ exit 10 } }catch{}; exit 0"
if "%errorlevel%"=="10" goto already_running

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo [1/3] 正在后台启动 uvicorn（独立窗口输出日志）...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PY%' -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','%PORT%') -WorkingDirectory '%BACKEND%'"

echo [2/3] 等待服务就绪（首次启动需加载嵌入与重排模型，可能 1~3 分钟）...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='%URL%/api/health'; for($i=0;$i -lt 150;$i++){ try{ $r=Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 3; if($r.StatusCode -eq 200){ exit 0 } }catch{}; Start-Sleep -Seconds 2 }; exit 1"
if "%errorlevel%"=="1" goto not_ready

echo [3/3] 服务已就绪，正在打开接口文档...
start "" "%DOCS%"
goto finished

:already_running
echo [跳过] 检测到后端已在 %PORT% 端口运行，直接打开接口文档。
echo.
start "" "%DOCS%"
goto finished

:not_ready
echo [警告] 等待超时。服务可能仍在启动，请查看后端日志窗口。
echo        稍后手动访问: %DOCS%

:finished
echo.
echo --------------------------------------------
echo 后端常用地址:
echo   接口文档  %DOCS%
echo   健康检查  %URL%/api/health
echo   模型列表  %URL%/api/models
echo.
echo 关闭服务: 关闭标题为 "python" 的日志窗口即可。
echo --------------------------------------------
powershell -NoProfile -Command "Start-Sleep -Seconds 8"
endlocal

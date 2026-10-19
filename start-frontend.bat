@echo off
setlocal
title PTDRA 前端服务

set "ROOT=%~dp0"
set "FRONTEND=%ROOT%frontend"
set "PORT=5173"
set "URL=http://127.0.0.1:%PORT%"

echo ============================================
echo    PTDRA 前端服务（React + Vite）
echo    目录: %FRONTEND%
echo    地址: %URL%
echo ============================================
echo.

if not exist "%FRONTEND%\package.json" (
    echo [错误] 未找到前端工程: %FRONTEND%\package.json
    echo.
    pause
    exit /b 1
)

rem ---------- npm 缓存与镜像固定到项目内，避免落到受限的全局目录 ----------
set "npm_config_cache=%ROOT%.npm-cache"
set "npm_config_registry=https://registry.npmmirror.com"

rem ---------- 端口已被占用时直接复用，不重复启动 ----------
powershell -NoProfile -ExecutionPolicy Bypass -Command "try{ $r=Invoke-WebRequest -Uri '%URL%' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){ exit 10 } }catch{}; exit 0"
if "%errorlevel%"=="10" goto already_running

if exist "%FRONTEND%\node_modules" goto deps_ready

echo [1/3] 未找到 node_modules，正在安装依赖（首次较慢）...
pushd "%FRONTEND%"
call npm install --no-audit --no-fund
set "INSTALL_RC=%errorlevel%"
popd
if not "%INSTALL_RC%"=="0" goto install_failed
echo       依赖安装完成。
goto deps_done

:deps_ready
echo [1/3] 依赖已就绪，跳过安装。

:deps_done
echo [2/3] 正在后台启动 Vite dev server（独立窗口输出日志）...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'npm.cmd' -ArgumentList @('run','dev','--','--host','127.0.0.1') -WorkingDirectory '%FRONTEND%'"

echo [3/3] 等待页面就绪...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='%URL%'; for($i=0;$i -lt 60;$i++){ try{ $r=Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 3; if($r.StatusCode -eq 200){ exit 0 } }catch{}; Start-Sleep -Seconds 2 }; exit 1"
if "%errorlevel%"=="1" goto not_ready

echo       页面已就绪，正在打开浏览器...
start "" "%URL%"
goto finished

:already_running
echo [跳过] 检测到前端已在 %PORT% 端口运行，直接打开页面。
echo.
start "" "%URL%"
goto finished

:not_ready
echo [警告] 等待超时。Vite 可能仍在编译，请查看前端日志窗口。
echo        稍后手动访问: %URL%
goto finished

:install_failed
echo.
echo [错误] 依赖安装失败，请检查网络后重试，或手动执行:
echo        cd "%FRONTEND%" ^&^& npm install
echo.
pause
exit /b 1

:finished
echo.
echo --------------------------------------------
echo 前端地址: %URL%
echo 后端代理: /api -^> http://localhost:8000 （需先启动后端）
echo.
echo 关闭服务: 关闭标题为 "npm run dev" 的日志窗口即可。
echo --------------------------------------------
powershell -NoProfile -Command "Start-Sleep -Seconds 8"
endlocal

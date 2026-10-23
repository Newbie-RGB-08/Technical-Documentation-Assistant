<#
.SYNOPSIS
    一键启动开发环境：后端 (默认 8000) + 前端 Vite (默认 5173)。

.DESCRIPTION
    统一设置模型缓存目录与前端代理目标，避免出现"后端换了端口、
    前端代理仍指向 8000"或"8000 上跑着旧进程"导致的 404 排查成本。

.EXAMPLE
    .\run-dev.ps1
    .\run-dev.ps1 -BackendPort 8003 -FrontendPort 5174
    .\run-dev.ps1 -Reload          # 后端开启热重载
    .\run-dev.ps1 -NoFrontend      # 只起后端
#>
[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$Reload,
    [switch]$NoFrontend
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"

if (-not (Test-Path $VenvPython)) {
    throw "未找到虚拟环境：$VenvPython（请先创建 .venv 并安装依赖）"
}

# 模型缓存固定在项目内，避免散落到用户目录
$env:HF_HOME = Join-Path $Root ".hf-cache"

# 前端代理跟随本次后端端口（vite.config.ts 读取 VITE_PROXY_TARGET）
$env:VITE_PROXY_TARGET = "http://127.0.0.1:$BackendPort"

if (-not $NoFrontend) {
    if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
        Write-Host "前端依赖缺失，执行 npm install ..."
        Push-Location $FrontendDir
        try { npm install } finally { Pop-Location }
    }
    Write-Host "启动前端: http://127.0.0.1:$FrontendPort （代理 -> $env:VITE_PROXY_TARGET）"
    $frontendCmd = "npm run dev -- --port $FrontendPort"
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-Command", $frontendCmd
    ) -WorkingDirectory $FrontendDir
}

Write-Host "启动后端: http://127.0.0.1:$BackendPort"
$uvicornArgs = @(
    "-m", "uvicorn", "app.main:app",
    "--host", "127.0.0.1", "--port", $BackendPort
)
if ($Reload) { $uvicornArgs += "--reload" }

Push-Location $BackendDir
try {
    & $VenvPython @uvicornArgs
} finally {
    Pop-Location
}

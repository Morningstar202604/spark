# Spark Windows 一键构建脚本（生成 spark.exe）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "[1/3] 创建虚拟环境..." -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { python -m venv .venv }
& ".venv\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
& ".venv\Scripts\python.exe" -m pip install -e ".[dev]" pyinstaller | Out-Null

Write-Host "[2/3] 运行测试门禁..." -ForegroundColor Cyan
& ".venv\Scripts\python.exe" -m pytest -q | Out-Null

Write-Host "[3/3] 打包 spark.exe..." -ForegroundColor Cyan
& ".venv\Scripts\pyinstaller.exe" --clean -y spark.spec

Write-Host ""
Write-Host "完成！可执行文件：dist\spark\spark.exe" -ForegroundColor Green
Write-Host "启动：dist\spark\spark.exe web（然后浏览器打开打印的地址）" -ForegroundColor Green

# Spark Quick Start — launches backend + frontend in one shot
# Usage: powershell -ExecutionPolicy Bypass -File scripts/start-spark.ps1

$ErrorActionPreference = "SilentlyContinue"
$PROJECT_ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$FE_DIR = Join-Path $PROJECT_ROOT "spark\web"

function Kill-Port($port) {
    $p = netstat -ano | Select-String ":$port\s+.*LISTING" | ForEach-Object { ($_ -split '\s+')[-1] } | Select-Object -First 1
    if ($p) { taskkill //PID $p //F 2>$null; Start-Sleep -Milliseconds 500 }
}
function Wait-For($port, $t=15) {
    $s = [Diagnostics.Stopwatch]::StartNew()
    while ($s.Elapsed.TotalSeconds -lt $t) {
        if (netstat -ano | Select-String ":$port\s+.*LISTING") { return $true }
        Start-Sleep -Milliseconds 500
    }; return $false
}

Kill-Port 8000; Kill-Port 5173
Write-Host "Starting Spark backend..." -ForegroundColor Cyan
Start-Process python -ArgumentList "-m spark web --host 127.0.0.1 --port 8000" -WindowStyle Hidden
if (Wait-For 8000 15) { Write-Host "  backend  OK  http://127.0.0.1:8000" -ForegroundColor Green } else { Write-Host "  backend  FAILED" -ForegroundColor Red; exit 1 }

Write-Host "Starting Spark frontend..." -ForegroundColor Cyan
Start-Process npm -ArgumentList "run dev" -WorkingDirectory $FE_DIR -WindowStyle Hidden
if (Wait-For 5173 15) { Write-Host "  frontend OK  http://localhost:5173" -ForegroundColor Green } else { Write-Host "  frontend FAILED" -ForegroundColor Red; exit 1 }

Write-Host "`nSpark is ready!" -ForegroundColor Yellow
Write-Host "  Web UI:      http://localhost:5173"
Write-Host "  API backend: http://127.0.0.1:8000"
Write-Host "Stop with: taskkill //PID (netstat -ano | findstr :8000 | ForEach{ (\$_ -split '\s+')[-1] }) //F"

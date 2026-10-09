# Spark Screenshot Demo Automation
# Automatically starts backend + frontend, captures screenshots for README
# Usage: powershell -ExecutionPolicy Bypass -File scripts/screenshot-demo.ps1

$ErrorActionPreference = "SilentlyContinue"
$PROJECT_ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ASSETS_DIR = Join-Path $PROJECT_ROOT "assets\screenshots"
$SHOT_DIR = "C:\Users\Administrator\.agent-browser\tmp\screenshots"

# ---- helpers ----
function Kill-Port($port) {
    $p = netstat -ano | Select-String ":$port\s+.*LISTING" | ForEach-Object {
        ($_ -split '\s+')[-1]
    } | Select-Object -First 1
    if ($p) { taskkill //PID $p //F 2>$null; Start-Sleep -Milliseconds 500 }
}

function Wait-For-Port($port, $timeoutSec=15) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $timeoutSec) {
        $ok = netstat -ano | Select-String ":$port\s+.*LISTING" | Select-Object -First 1
        if ($ok) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Take-Shot($name) {
    Start-Sleep -Seconds 1
    $json = paw browser-action '{"action":"screenshot","fullPage":false}' 2>&1
    $raw = $json | Out-String
    if ($raw -match '(C:\\[^\s"]+\.png)') {
        $src = $Matches[1]
        $dest = Join-Path $ASSETS_DIR "$name.png"
        Copy-Item $src $dest -Force
        Write-Host "  -> $name.png"
    } else {
        Write-Host "  -> FAILED for $name"
    }
}

# ---- setup ----
Write-Host "=== Spark Screenshot Demo ==="
Kill-Port 8000; Kill-Port 5173
New-Item -ItemType Directory -Force -Path $ASSETS_DIR | Out-Null

# ---- start backend ----
Write-Host "`n[1/5] Starting backend..."
Start-Process -FilePath python -ArgumentList "-m spark web --host 127.0.0.1 --port 8000 --log-level warning" -WindowStyle Hidden
if (Wait-For-Port 8000 15) { Write-Host "  backend OK on :8000" } else { Write-Host "  backend FAILED"; exit 1 }

# ---- start frontend ----
Write-Host "[2/5] Starting frontend..."
$fe = Join-Path $PROJECT_ROOT "spark\web"
Start-Process -FilePath npm -ArgumentList "run dev" -WorkingDirectory $fe -WindowStyle Hidden
if (Wait-For-Port 5173 15) { Write-Host "  frontend OK on :5173" } else { Write-Host "  frontend FAILED"; exit 1 }

# ---- browser setup ----
Write-Host "[3/5] Configuring browser..."
paw browser-action '{"action":"viewport","width":1440,"height":900}' 2>$null
paw browser-action '{"action":"navigate","url":"http://localhost:5173","waitUntil":"networkidle"}' 2>$null
Start-Sleep -Seconds 2

# ---- capture shots ----
Write-Host "[4/5] Capturing screenshots..."
Take-Shot "01-welcome"

# Click on an existing session (from prior testing) to show chat
$snap = (paw browser-action '{"action":"snapshot","interactive":true}' 2>&1) | ConvertFrom-Json
$firstSession = $snap.data.refs.PSObject.Properties | Where-Object { $_.Value.role -eq "generic" -and $_.Value.name -match '10月' } | Select-Object -First 1
if ($firstSession) {
    paw browser-action ("{""action"":""click"",""selector"":""@" + $firstSession.Name + ""}" | ConvertTo-Json -Compress) | Out-Null
    Start-Sleep -Seconds 2
    Take-Shot "02-chat"
}

# Open settings
$snap = (paw browser-action '{"action":"snapshot","interactive":true}' 2>&1) | ConvertFrom-Json
$settingsBtn = $snap.data.refs.PSObject.Properties | Where-Object { $_.Value.name -eq "设置" -and $_.Value.role -eq "button" } | Select-Object -First 1
if ($settingsBtn) {
    paw browser-action ("{""action"":""click"",""selector"":""@" + $settingsBtn.Name + ""}" | ConvertTo-Json -Compress) | Out-Null
    Start-Sleep -Seconds 2
    Take-Shot "03-settings"
    paw browser-action '{"action":"press","key":"Escape"}' 2>$null
}

# New conversation view
$snap = (paw browser-action '{"action":"snapshot","interactive":true}' 2>&1) | ConvertFrom-Json
$newBtn = $snap.data.refs.PSObject.Properties | Where-Object { $_.Value.name -match "新建会话" -and $_.Value.role -eq "button" } | Select-Object -First 1
if ($newBtn) {
    paw browser-action ("{""action"":""click"",""selector"":""@" + $newBtn.Name + ""}" | ConvertTo-Json -Compress) | Out-Null
    Start-Sleep -Seconds 1
    $input = $snap.data.refs.PSObject.Properties | Where-Object { $_.Value.role -eq "textbox" -and $_.Value.name -match "Spark" } | Select-Object -First 1
    if ($input) {
        paw browser-action ("{""action"":""fill"",""selector"":""@" + $input.Name + """,""value"":""介绍一下 Spark 的工具系统""}" | ConvertTo-Json -Compress) | Out-Null
        Start-Sleep -Seconds 1
        Take-Shot "04-chat-input"
    }
}

# ---- done ----
Write-Host "`n[5/5] Screenshots saved to: $ASSETS_DIR"
Get-ChildItem $ASSETS_DIR -Filter *.png | ForEach-Object { Write-Host "  $_" }
Write-Host "`nDone. Servers remain running (backend :8000, frontend :5173)."

# Spark 一键安装（Windows PowerShell）
#
#   irm https://raw.githubusercontent.com/badhope/spark/main/scripts/install.ps1 | iex
#
# 行为：取最新 Release → 校验 SHA256SUMS → 解压到
# $env:LOCALAPPDATA\Spark → 把目录加入用户 PATH。
# 不请求管理员权限、不改系统目录、校验失败立即中止。
# 可用 $env:SPARK_VERSION="v0.9.12" 固定版本；$env:SPARK_MIRROR 指向内网镜像。
param(
    [string]$InstallDir = $(if ($env:SPARK_INSTALL_DIR) { $env:SPARK_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Spark" }),
    [switch]$NoPath
)

$ErrorActionPreference = "Stop"
$repo = "badhope/spark"
$asset = "spark-windows-x86_64.zip"
$api = if ($env:SPARK_VERSION) { "https://api.github.com/repos/$repo/releases/tags/$env:SPARK_VERSION" } else { "https://api.github.com/repos/$repo/releases/latest" }

function Step($msg) { Write-Host "-> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "error: $msg" -ForegroundColor Red; exit 1 }

Step "查询最新版本…"
try {
    $release = Invoke-RestMethod -Uri $api -Headers @{ Accept = "application/vnd.github+json" } -TimeoutSec 30
} catch {
    Fail "无法访问 GitHub Releases：$($_.Exception.Message)"
}

$url = $null
$sumsUrl = $null
foreach ($a in $release.assets) {
    if ($a.name -eq $asset) { $url = $a.browser_download_url }
    if ($a.name -eq "SHA256SUMS") { $sumsUrl = $a.browser_download_url }
}
if ($env:SPARK_MIRROR) {
    $url = "$($env:SPARK_MIRROR.TrimEnd('/'))/$asset"
    $sumsUrl = "$($env:SPARK_MIRROR.TrimEnd('/'))/SHA256SUMS"
}
if (-not $url) { Fail "Release 中没有 $asset（可用产物见 https://github.com/$repo/releases）" }
if (-not $sumsUrl) { Fail "Release 中没有 SHA256SUMS，拒绝安装未校验的产物" }

$tmp = Join-Path $env:TEMP ("spark-install-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
try {
    Step "下载 $asset …"
    $zip = Join-Path $tmp $asset
    Invoke-WebRequest -Uri $url -OutFile $zip -TimeoutSec 300
    $sumsFile = Join-Path $tmp "SHA256SUMS"
    Invoke-WebRequest -Uri $sumsUrl -OutFile $sumsFile -TimeoutSec 60

    Step "校验 SHA256…"
    $line = Get-Content $sumsFile | Where-Object { $_ -match "\s\*?$([regex]::Escape($asset))$" } | Select-Object -First 1
    if (-not $line) { Fail "SHA256SUMS 中没有 $asset" }
    $expected = ($line -split '\s+')[0].ToLower()
    $actual = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) { Fail "SHA256 不匹配（期望 $expected，实际 $actual）" }

    Step "安装到 $InstallDir …"
    if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Expand-Archive -Path $zip -DestinationPath $InstallDir -Force

    $exe = Get-ChildItem -Path $InstallDir -Filter "spark.exe" -Recurse | Select-Object -First 1
    if (-not $exe) { Fail "解压后找不到 spark.exe" }

    if (-not $NoPath) {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $exeDir = $exe.Directory.FullName
        if ($userPath -notlike "*$exeDir*") {
            [Environment]::SetEnvironmentVariable("Path", "$userPath;$exeDir", "User")
            $env:Path = "$env:Path;$exeDir"
            Step "已把 $exeDir 加入用户 PATH（新开终端生效）"
        }
    }

    Step "已安装：$(& $exe.FullName --version)"
    Write-Host ""
    Write-Host "下一步：spark init 配置模型 → spark web 打开界面" -ForegroundColor Green
    Write-Host "桌面版：$($exe.Directory.FullName)\spark-desktop.exe" -ForegroundColor Green
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

param(
    [int]$Port = 8770,
    [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if ($Port -lt 1 -or $Port -gt 65535) {
    throw "端口必须在 1 到 65535 之间。"
}

$listener = @(Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($listener.Count -gt 0) {
    $owners = ($listener | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
    throw "端口 $Port 已被占用（进程 $owners）。请关闭现有服务或改用 -Port。"
}

$python = (Get-Command python -ErrorAction Stop).Source
$arguments = @("-B", "-X", "utf8", "-m", "design.server", "--port", "$Port")
if ($DataDir) {
    $arguments += @("--data-dir", $DataDir)
}

Write-Host "MarketMirror platform: http://127.0.0.1:$Port/"
Write-Host "服务在当前终端前台运行；按 Ctrl+C 停止。"
& $python @arguments
exit $LASTEXITCODE

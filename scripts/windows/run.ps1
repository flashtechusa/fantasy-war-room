<#
.SYNOPSIS
    Start the Fantasy War Room server. Invoked by the FantasyWarRoom scheduled task.

.DESCRIPTION
    Kept separate from install.ps1 so the boot task runs a small, stable script.
    You can also run it by hand to watch the app start in the foreground.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [int]$Port          = 8000
)

$ErrorActionPreference = 'Stop'

# $PSScriptRoot is ...\scripts\windows, so the repo root is two levels up.
if (-not (Test-Path (Join-Path $InstallDir 'pyproject.toml'))) {
    $InstallDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

Set-Location $InstallDir

$python = Join-Path $InstallDir '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "No virtualenv at $python. Run install.ps1 first."
}

New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir 'data') | Out-Null
$log = Join-Path $InstallDir 'data\app.log'

# SQLite lives beside the code so it survives updates.
$env:FWR_DATABASE_URL = 'sqlite:///./data/fantasy_war_room.db'
$env:PYTHONUNBUFFERED  = '1'

"`n=== started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') on port $Port ===" |
    Out-File -FilePath $log -Append -Encoding utf8

# 0.0.0.0 so the app is reachable from your phone, not just the console.
& $python -m uvicorn app.main:app `
    --app-dir backend `
    --host 0.0.0.0 `
    --port $Port `
    *>> $log

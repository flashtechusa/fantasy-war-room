<#
.SYNOPSIS
    Start the Fantasy War Room server. Invoked by the FantasyWarRoom scheduled task.

.DESCRIPTION
    Kept separate from install.ps1 so the boot task runs a small, stable script.
    You can also run it by hand to watch the app start in the foreground.
#>
[CmdletBinding()]
param(
    # Resolved in the body, not here: $PSScriptRoot is still empty while
    # parameter defaults are being bound under Windows PowerShell 5.1, so a
    # default of (Split-Path $PSScriptRoot) throws before the script can run.
    [string]$InstallDir,
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'

if (-not $InstallDir) {
    # $PSCommandPath is populated for scripts; $MyInvocation covers older hosts.
    $scriptPath = $PSCommandPath
    if (-not $scriptPath) { $scriptPath = $MyInvocation.MyCommand.Path }
    if (-not $scriptPath) { throw 'Cannot determine the install directory. Pass -InstallDir.' }
    # ...\scripts\windows\run.ps1 -> ...\scripts\windows -> ...\scripts -> root
    $InstallDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $scriptPath))
}

if (-not (Test-Path (Join-Path $InstallDir 'pyproject.toml'))) {
    throw "No pyproject.toml under '$InstallDir' -- that does not look like the install directory."
}

Set-Location $InstallDir

New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir 'data') | Out-Null
$log = Join-Path $InstallDir 'data\app.log'

# Record the attempt before anything else can fail, so a crash during startup
# leaves a trace instead of vanishing the way an early param error used to.
"`n=== starting $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | dir=$InstallDir | port=$Port ===" |
    Out-File -FilePath $log -Append -Encoding utf8

$python = Join-Path $InstallDir '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    "FATAL: no virtualenv at $python -- run install.ps1 first." |
        Out-File -FilePath $log -Append -Encoding utf8
    throw "No virtualenv at $python. Run install.ps1 first."
}

# SQLite lives beside the code so it survives updates.
$env:FWR_DATABASE_URL = 'sqlite:///./data/fantasy_war_room.db'
$env:PYTHONUNBUFFERED  = '1'

# uvicorn logs to stderr, and under 'Stop' PowerShell turns any stderr from a
# native command into a terminating error -- which killed a perfectly healthy
# server on its own "Started server process" line. Relax it for the exec.
$ErrorActionPreference = 'Continue'

# 0.0.0.0 so the app is reachable from your phone, not just the console.
& $python -m uvicorn app.main:app `
    --app-dir backend `
    --host 0.0.0.0 `
    --port $Port `
    *>> $log

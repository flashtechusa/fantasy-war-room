<#
.SYNOPSIS
    Run the autonomous Auto Mode cycle on a schedule.

.DESCRIPTION
    This is the scheduler that makes Auto Mode "auto": it invokes the app's
    `python -m app.automode_cycle` entry point, which sets the optimal lineup on
    ESPN for every fully-enabled user (install switch on, account granted, user
    opted in). It is safe to run often -- with nothing enabled, or a lineup
    already optimal, it performs no ESPN write and exits in about a second.

    Only the LINEUP tier writes today; waivers and trades are planned/logged only
    (see automode_runner.py). Setting a lineup is idempotent and reversible, so a
    frequent cadence is fine.

    Like auto-update.ps1 this skips the Sunday game window: a lineup should be
    finalised BEFORE kickoff, and ESPN locks players once their game starts, so
    there is nothing useful to do mid-games.

.EXAMPLE
    # Register: check every 60 minutes (default).
    powershell -NoProfile -ExecutionPolicy Bypass -File auto-mode.ps1 -Register

    # Run one cycle now.
    powershell -NoProfile -ExecutionPolicy Bypass -File auto-mode.ps1
#>
[CmdletBinding()]
param(
    [string]$InstallDir = 'C:\FantasyWarRoom',
    [int]$Port          = 8000,
    # Register the recurring schedule instead of running a cycle now.
    [switch]$Register,
    # How often to run, in minutes.
    [int]$EveryMinutes = 60,
    # Run even inside the Sunday game window.
    [switch]$Force
)

$ErrorActionPreference = 'Continue'

$logDir = Join-Path $InstallDir 'data'
$log    = Join-Path $logDir 'auto-mode.log'

function Write-Log($Message) {
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $line
    try { $line | Out-File -FilePath $log -Append -Encoding utf8 } catch { }
}

# ---------------------------------------------------------------- register --
if ($Register) {
    $self = $PSCommandPath
    if (-not $self) { $self = $MyInvocation.MyCommand.Path }

    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $InstallDir `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$self`""

    # Repeat all day; each run is cheap and idempotent, so a lineup change lands
    # within the interval instead of at the next daily tick.
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(7) `
        -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)

    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

    Register-ScheduledTask -TaskName 'FantasyWarRoomAutoMode' -Action $action `
        -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null

    Write-Host "Registered. Running the Auto Mode cycle every $EveryMinutes minutes." -ForegroundColor Green
    Write-Host "Sunday 11:00-20:00 is skipped (lineups lock at kickoff)."
    Write-Host "Log: $log"
    exit 0
}

# ------------------------------------------------------------------- guard --
$now = Get-Date
if (-not $Force -and $now.DayOfWeek -eq 'Sunday' -and $now.Hour -ge 11 -and $now.Hour -lt 20) {
    Write-Log "Sunday game window -- skipping Auto Mode cycle."
    exit 0
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$python = Join-Path $InstallDir '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { Write-Log "No virtualenv at $python"; exit 1 }

# Point the app at the same database the service uses (absolute, like the
# service definition -- a relative sqlite path opens an empty database elsewhere).
$env:FWR_DATABASE_URL = "sqlite:///$($InstallDir -replace '\\','/')/data/fantasy_war_room.db"

Write-Log "=== Auto Mode cycle starting ==="
Push-Location (Join-Path $InstallDir 'backend')
try {
    & $python -m app.automode_cycle 2>&1 | ForEach-Object { Write-Log $_ }
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
Write-Log "=== Auto Mode cycle finished (exit $code) ==="
exit $code

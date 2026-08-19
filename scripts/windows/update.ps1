<#
.SYNOPSIS
    Update Fantasy War Room on a Windows VPS, safely and in one command.

.DESCRIPTION
    Doing this by hand is a specific six-step sequence, and getting the order
    wrong takes the app down: copying files or running pip while the server is
    still holding them leaves a half-updated install that starts and then
    serves the previous build. This stops the service first, updates, and only
    then brings it back -- verifying it actually answers before reporting
    success.

    Your data is never touched: data\ is excluded from the copy and the
    database is a separate file.

    Git is not required. The code comes from the branch zip.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File C:\FantasyWarRoom\scripts\windows\update.ps1

.NOTES
    Run from an Administrator prompt. Re-running is safe.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = 'C:\FantasyWarRoom',
    [string]$Branch     = 'claude/fantasy-war-room-app-n6edzt',
    [int]$Port          = 8000,
    [string]$TaskName   = 'FantasyWarRoom'
)

function Write-Step($Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Write-Ok($Message)   { Write-Host "    $Message" -ForegroundColor Green }
function Write-Bad($Message)  { Write-Host "    $Message" -ForegroundColor Red }

$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this from an Administrator PowerShell prompt.'
}
if (-not (Test-Path (Join-Path $InstallDir 'pyproject.toml'))) {
    throw "No install found at $InstallDir."
}

$python = Join-Path $InstallDir '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw "No virtualenv at $python. Run install.ps1 first." }

function Get-Bundle {
    $indexPath = Join-Path $InstallDir 'backend\app\static\index.html'
    if (-not (Test-Path $indexPath)) { return '(none)' }
    $match = Select-String -Path $indexPath -Pattern 'index-[A-Za-z0-9_-]+\.js' |
        Select-Object -First 1
    if ($match) { return $match.Matches[0].Value }
    return '(unknown)'
}

$before = Get-Bundle
Write-Ok "Currently serving: $before"

# --- stop first: this is the step whose absence causes half-updated installs
Write-Step 'Stopping the app'
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
# Task Scheduler kills cmd.exe but not always its python child, and a survivor
# holds both the port and the files we are about to replace.
# Reading .Path on a process this account cannot inspect yields null, and
# PowerShell still evaluates the method call on it -- so guard rather than
# relying on -and to short-circuit.
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $exe = $null
    try { $exe = $_.Path } catch { }
    $exe -and $exe.StartsWith($InstallDir, 'OrdinalIgnoreCase')
} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
Write-Ok 'Stopped.'

# --- fetch ----------------------------------------------------------------
Write-Step 'Downloading the latest code'
$zip     = Join-Path $env:TEMP 'fwr-update.zip'
$extract = Join-Path $env:TEMP 'fwr-update'
$url     = "https://codeload.github.com/flashtechusa/fantasy-war-room/zip/refs/heads/$Branch"

try {
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip -ErrorAction Stop
} catch {
    Write-Bad "Download failed: $($_.Exception.Message)"
    Write-Bad 'GitHub rate-limits datacenter IPs; wait a few minutes and retry.'
    Write-Step 'Restarting the current version'
    Start-ScheduledTask -TaskName $TaskName
    exit 1
}

if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
Expand-Archive $zip -DestinationPath $extract -Force
$source = (Get-ChildItem $extract -Directory | Select-Object -First 1).FullName
Write-Ok 'Downloaded.'

# --- copy, excluding anything of yours ------------------------------------
Write-Step 'Updating files'
Get-ChildItem $source -Force | Where-Object { $_.Name -ne 'data' } | ForEach-Object {
    Copy-Item $_.FullName -Destination $InstallDir -Recurse -Force
}
Write-Ok 'Files updated.'

Write-Step 'Installing dependencies'
& $python -m pip install --quiet $InstallDir
if ($LASTEXITCODE -ne 0) { Write-Bad 'pip reported a problem; continuing to restart anyway.' }
else { Write-Ok 'Dependencies current.' }

# --- re-register the task with absolute paths -----------------------------
# The database URL is pinned here as well as in code: a relative path resolves
# against the service's working directory, which is not the install directory,
# and that silently opens an empty database.
Write-Step 'Refreshing the service definition'
$dbUrl = "sqlite:///$($InstallDir -replace '\\','/')/data/fantasy_war_room.db"
$arg = "/c set `"FWR_DATABASE_URL=$dbUrl`" && " +
       "`"$python`" -m uvicorn app.main:app " +
       "--app-dir `"$InstallDir\backend`" --host 0.0.0.0 --port $Port " +
       ">> `"$InstallDir\data\app.log`" 2>&1"

$action = New-ScheduledTaskAction -Execute 'cmd.exe' -WorkingDirectory $InstallDir -Argument $arg
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
Write-Ok 'Service definition current.'

# --- start and verify -----------------------------------------------------
Write-Step 'Starting the app'
Start-ScheduledTask -TaskName $TaskName

$healthy = $false
foreach ($attempt in 1..20) {
    Start-Sleep -Seconds 2
    try {
        $response = Invoke-WebRequest -UseBasicParsing `
            -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 4
        if ($response.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
}

$after = Get-Bundle

if (-not $healthy) {
    Write-Bad "The app did not answer on port $Port."
    Write-Bad "Check: Get-Content $InstallDir\data\app.log -Tail 40"
    exit 1
}

Write-Host "`nUpdated and running." -ForegroundColor Green
Write-Host "  Bundle: $before -> $after"
if ($before -eq $after) {
    Write-Host "  (Same bundle -- you were already on the latest frontend.)"
}

# A healthy app pointed at an empty database is the failure this script exists
# to make visible. It cannot be checked over HTTP any more -- /api/health is
# deliberately anonymous-safe and reports no league to a caller that has not
# signed in -- so ask the database directly.
try {
    $probe = "import sys; sys.path.insert(0, r'$InstallDir\backend'); " +
             "from app.db import get_session_factory, init_db; " +
             "from app.models import League; init_db(); " +
             "s = get_session_factory()(); " +
             "rows = s.query(League).all(); " +
             "print('LEAGUES:' + '; '.join(f'{l.name} ({l.season})' for l in rows) if rows else 'LEAGUES:none')"
    $env:FWR_DATABASE_URL = $dbUrl
    $found = & $python -c $probe 2>$null | Select-String '^LEAGUES:'
    if ($found -and $found.Line -ne 'LEAGUES:none') {
        Write-Host "  $($found.Line -replace '^LEAGUES:', 'Leagues: ')" -ForegroundColor Green
    } elseif ($found) {
        Write-Bad '  The database has no leagues in it. If you had one, the path is wrong.'
    }
} catch { }

Write-Host "`nOn your phone, fully close the app and reopen it -- home-screen"
Write-Host "web apps cache aggressively and a refresh is often not enough."

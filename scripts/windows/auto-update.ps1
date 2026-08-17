<#
.SYNOPSIS
    Check for a new version, back up, update, and roll back if it breaks.

.DESCRIPTION
    Unattended updating needs more care than the manual path, because nobody is
    watching when it goes wrong. This script:

      1. Backs up the database first (kept for 14 days).
      2. Skips entirely when the remote commit has not changed, so a healthy
         install is not restarted nightly for nothing.
      3. Keeps the previous code so a bad version can be undone.
      4. Health-checks afterwards and ROLLS BACK automatically if the new
         version does not answer.
      5. Writes every run to a log, so a silent failure is still discoverable.

    Deliberately does not run on Sunday: an app that is down at 12:55pm because
    it updated itself that morning is worse than one running last week's code.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File auto-update.ps1 -Register
    powershell -NoProfile -ExecutionPolicy Bypass -File auto-update.ps1
#>
[CmdletBinding()]
param(
    [string]$InstallDir = 'C:\FantasyWarRoom',
    [string]$Branch     = 'claude/fantasy-war-room-app-n6edzt',
    [string]$Repo       = 'flashtechusa/fantasy-war-room',
    [int]$Port          = 8000,
    [string]$TaskName   = 'FantasyWarRoom',
    # Register the recurring schedule instead of running an update now.
    [switch]$Register,
    # Update even when the remote commit has not changed.
    [switch]$Force
)

$ErrorActionPreference = 'Continue'

$logDir    = Join-Path $InstallDir 'data'
$log       = Join-Path $logDir 'auto-update.log'
$stampFile = Join-Path $logDir 'last-applied-commit.txt'
$backupDir = Join-Path $InstallDir 'backups'

function Write-Log($Message, $Level = 'INFO') {
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $line
    try { $line | Out-File -FilePath $log -Append -Encoding utf8 } catch { }
}

# ---------------------------------------------------------------- register --
if ($Register) {
    $self = $PSCommandPath
    if (-not $self) { $self = $MyInvocation.MyCommand.Path }

    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $InstallDir `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$self`""

    # 04:30 Mon/Tue/Wed/Thu/Fri/Sat. Never Sunday -- see the note above.
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday,Saturday -At 4:30AM
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

    Register-ScheduledTask -TaskName 'FantasyWarRoomAutoUpdate' -Action $action `
        -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null

    Write-Host "Registered. Updates check at 04:30, Monday through Saturday." -ForegroundColor Green
    Write-Host "Log: $log"
    exit 0
}

# ------------------------------------------------------------------- checks --
New-Item -ItemType Directory -Force -Path $logDir, $backupDir | Out-Null
Write-Log "=== auto-update starting ==="

if (-not (Test-Path (Join-Path $InstallDir 'pyproject.toml'))) {
    Write-Log "No install at $InstallDir" 'FATAL'; exit 1
}
$python = Join-Path $InstallDir '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { Write-Log "No virtualenv at $python" 'FATAL'; exit 1 }

# ------------------------------------------------------- is there anything? --
$remoteSha = $null
try {
    $api = "https://api.github.com/repos/$Repo/commits/$Branch"
    $head = Invoke-RestMethod -Uri $api -Headers @{ 'User-Agent' = 'fwr-auto-update' } -TimeoutSec 20
    $remoteSha = $head.sha
} catch {
    # GitHub rate-limits datacenter IPs. Not knowing the remote commit is not a
    # reason to skip an update, but it is a reason to say so.
    Write-Log "Could not read the remote commit ($($_.Exception.Message)); updating anyway." 'WARN'
}

$lastSha = if (Test-Path $stampFile) { (Get-Content $stampFile -Raw).Trim() } else { '' }
if ($remoteSha -and $lastSha -eq $remoteSha -and -not $Force) {
    Write-Log "Already on $($remoteSha.Substring(0,8)); nothing to do."
    exit 0
}

# ------------------------------------------------------------------ backup --
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$db = Join-Path $InstallDir 'data\fantasy_war_room.db'
if (Test-Path $db) {
    $dbBackup = Join-Path $backupDir "fantasy_war_room-$stamp.db"
    Copy-Item $db $dbBackup -Force
    Write-Log "Database backed up to $dbBackup"
} else {
    Write-Log "No database found to back up." 'WARN'
}

# Keep the previous code so a bad version can be undone.
$codeBackup = Join-Path $backupDir "code-$stamp"
New-Item -ItemType Directory -Force -Path $codeBackup | Out-Null
Copy-Item (Join-Path $InstallDir 'backend') $codeBackup -Recurse -Force
Write-Log "Previous code kept at $codeBackup"

# Prune anything older than 14 days.
Get-ChildItem $backupDir -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# ------------------------------------------------------------------ update --
Write-Log "Stopping the app"
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Get-Process python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($InstallDir, 'OrdinalIgnoreCase') } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

$zip     = Join-Path $env:TEMP 'fwr-auto.zip'
$extract = Join-Path $env:TEMP 'fwr-auto'
$url     = "https://codeload.github.com/$Repo/zip/refs/heads/$Branch"

try {
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip -ErrorAction Stop
    if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
    Expand-Archive $zip -DestinationPath $extract -Force
} catch {
    Write-Log "Download failed: $($_.Exception.Message). Restarting current version." 'ERROR'
    Start-ScheduledTask -TaskName $TaskName
    exit 1
}

$source = (Get-ChildItem $extract -Directory | Select-Object -First 1).FullName
Get-ChildItem $source -Force | Where-Object { $_.Name -ne 'data' } | ForEach-Object {
    Copy-Item $_.FullName -Destination $InstallDir -Recurse -Force
}
Write-Log "Files updated"

& $python -m pip install --quiet $InstallDir 2>&1 | Out-Null
Write-Log "Dependencies installed"

Start-ScheduledTask -TaskName $TaskName

# ------------------------------------------------------------------ verify --
$healthy = $false
$leagueLoaded = $false
foreach ($attempt in 1..20) {
    Start-Sleep -Seconds 3
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            $body = $response.Content | ConvertFrom-Json
            $leagueLoaded = [bool]$body.league_imported
            break
        }
    } catch { }
}

if ($healthy -and $leagueLoaded) {
    if ($remoteSha) { $remoteSha | Out-File -FilePath $stampFile -Encoding ascii -NoNewline }
    Write-Log "Updated and healthy (league loaded)."
    exit 0
}

# --------------------------------------------------------------- roll back --
# A healthy app with no league is the empty-database failure, and is treated as
# a failure precisely because it looks fine from outside.
$why = if (-not $healthy) { "the app did not answer" } else { "no league was loaded" }
Write-Log "Update failed -- $why. Rolling back." 'ERROR'

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Get-Process python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($InstallDir, 'OrdinalIgnoreCase') } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

Copy-Item (Join-Path $codeBackup 'backend') $InstallDir -Recurse -Force
& $python -m pip install --quiet $InstallDir 2>&1 | Out-Null
Start-ScheduledTask -TaskName $TaskName

Start-Sleep -Seconds 15
try {
    $check = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 5
    if ($check.StatusCode -eq 200) {
        Write-Log "Rolled back to the previous version, which is running."
        exit 1
    }
} catch { }

Write-Log "ROLLBACK ALSO FAILED. Manual attention needed. Database backup: $backupDir" 'FATAL'
exit 2

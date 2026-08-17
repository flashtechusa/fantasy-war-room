<#
.SYNOPSIS
    Install Fantasy War Room on a Windows VPS and keep it running.

.DESCRIPTION
    Installs Python and Git if missing, clones (or updates) the repo, builds a
    virtualenv, and registers a scheduled task so the app starts at boot and
    restarts itself if it ever falls over.

    The frontend is committed to the repo already built, so Node is not needed.

.EXAMPLE
    # From an elevated PowerShell prompt:
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
    .\install.ps1

.NOTES
    Re-running this is safe -- it updates an existing install in place.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = 'C:\FantasyWarRoom',
    [string]$Branch     = 'claude/fantasy-war-room-app-n6edzt',
    [int]$Port          = 8000,
    # Only opens the Windows firewall when you ask for it. See README before
    # exposing this to the internet: ESPN session cookies over plain HTTP is a
    # real risk.
    [switch]$OpenFirewall
)

$ErrorActionPreference = 'Stop'
$RepoUrl = 'https://github.com/flashtechusa/fantasy-war-room.git'

function Write-Step($Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Write-Ok($Message)   { Write-Host "    $Message" -ForegroundColor Green }

# --- must be elevated: registering a boot task and a firewall rule need it ---
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this from an Administrator PowerShell prompt (right-click -> Run as administrator).'
}

# --- prerequisites --------------------------------------------------------
function Test-Command($Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Install-Prereq($DisplayName, $Command, $WingetId) {
    if (Test-Command $Command) { Write-Ok "$DisplayName already present."; return }
    if (-not (Test-Command 'winget')) {
        throw "$DisplayName is missing and winget is unavailable on this server. " +
              "Install $DisplayName manually, then re-run this script."
    }
    Write-Step "Installing $DisplayName..."
    winget install --id $WingetId --silent --accept-source-agreements --accept-package-agreements
    # winget updates PATH for new processes only; refresh it for this one.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not (Test-Command $Command)) {
        throw "$DisplayName installed but '$Command' is still not on PATH. Open a new " +
              'PowerShell window and re-run this script.'
    }
    Write-Ok "$DisplayName installed."
}

Write-Step 'Checking prerequisites'
Install-Prereq 'Git'    'git'    'Git.Git'
Install-Prereq 'Python' 'python' 'Python.Python.3.12'

$pythonVersion = (python -c 'import sys; print("%d.%d" % sys.version_info[:2])').Trim()
if ([version]$pythonVersion -lt [version]'3.10') {
    throw "Python $pythonVersion found, but 3.10 or newer is required."
}
Write-Ok "Python $pythonVersion"

# --- code -----------------------------------------------------------------
if (Test-Path (Join-Path $InstallDir '.git')) {
    Write-Step "Updating existing install at $InstallDir"
    Push-Location $InstallDir
    try {
        git fetch origin $Branch
        git checkout $Branch
        git pull --ff-only origin $Branch
    } finally { Pop-Location }
} else {
    Write-Step "Cloning into $InstallDir"
    git clone --branch $Branch $RepoUrl $InstallDir
}
Write-Ok 'Code is current.'

# --- python environment ---------------------------------------------------
Write-Step 'Building the virtual environment'
$venv   = Join-Path $InstallDir '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $python)) { python -m venv $venv }
& $python -m pip install --upgrade pip --quiet
& $python -m pip install --quiet $InstallDir
if ($LASTEXITCODE -ne 0) { throw 'Dependency install failed.' }
Write-Ok 'Dependencies installed.'

New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir 'data') | Out-Null

# --- run at boot ----------------------------------------------------------
# Task Scheduler rather than a third-party service wrapper: it ships with
# Windows, survives reboots, and can restart the app if it dies.
Write-Step 'Registering the startup task'
$taskName = 'FantasyWarRoom'
$runner   = Join-Path $InstallDir 'scripts\windows\run.ps1'

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -Port $Port" `
    -WorkingDirectory $InstallDir

$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null

Stop-ScheduledTask  -TaskName $taskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $taskName
Write-Ok 'Scheduled task registered and started.'

# --- firewall (opt-in) ----------------------------------------------------
if ($OpenFirewall) {
    Write-Step "Opening TCP $Port"
    $ruleName = "Fantasy War Room ($Port)"
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound `
        -Action Allow -Protocol TCP -LocalPort $Port | Out-Null
    Write-Ok 'Firewall rule added.'
}

# --- verify ---------------------------------------------------------------
Write-Step 'Waiting for the app to answer'
$healthy = $false
foreach ($attempt in 1..30) {
    Start-Sleep -Seconds 2
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" `
            -UseBasicParsing -TimeoutSec 4
        if ($response.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
}

if ($healthy) {
    Write-Host "`nRunning." -ForegroundColor Green
    Write-Host "  On this machine : http://localhost:$Port"
    if ($OpenFirewall) { Write-Host "  From your phone : http://<this-server-public-ip>:$Port" }
    Write-Host "`nNext: open the app, go to the League tab, and enter your ESPN details."
    Write-Host 'It now starts automatically whenever the server reboots.'
} else {
    Write-Warning "No response on port $Port after 60s. Check the log:"
    Write-Warning "  Get-Content '$InstallDir\data\app.log' -Tail 40"
    exit 1
}

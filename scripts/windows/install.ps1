<#
.SYNOPSIS
    Install Fantasy War Room on a Windows VPS and keep it running.

.DESCRIPTION
    Installs Python if missing, fetches (or updates) the code, builds a
    virtualenv, and registers a scheduled task so the app starts at boot and
    restarts itself if it ever falls over.

    Python is the only hard requirement. The frontend is committed already
    built, so Node is not needed, and Git is optional -- without it the code is
    refreshed from the branch zip instead. On servers without winget, Python is
    downloaded straight from python.org.

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

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Find-Python {
    <#
        Returns a path to a real Python >= 3.10, or $null.

        Windows ships a stub at 'python.exe' that opens the Microsoft Store, so
        a bare Test-Command is not enough -- the interpreter has to actually
        report a version. The 'py' launcher is tried first because it is not
        shadowed by that stub.
    #>
    $candidates = @()
    if (Test-Command 'py')     { $candidates += ,@('py', @('-3', '-c')) }
    if (Test-Command 'python') { $candidates += ,@('python', @('-c')) }
    foreach ($dir in @("$env:ProgramFiles\Python312", "$env:ProgramFiles\Python311",
                       "$env:LOCALAPPDATA\Programs\Python\Python312")) {
        $exe = Join-Path $dir 'python.exe'
        if (Test-Path $exe) { $candidates += ,@($exe, @('-c')) }
    }

    foreach ($candidate in $candidates) {
        $exe, $prefix = $candidate
        try {
            # Not $args -- that is an automatic variable and cannot be assigned.
            $probeArgs = $prefix + @('import sys; print("%d.%d" % sys.version_info[:2])')
            $reported = (& $exe @probeArgs 2>$null | Select-Object -First 1)
            if ($reported -and [version]$reported -ge [version]'3.10') {
                # Resolve to a concrete interpreter path for the venv step.
                $resolveArgs = $prefix + @('import sys; print(sys.executable)')
                $full = (& $exe @resolveArgs 2>$null | Select-Object -First 1)
                if ($full -and (Test-Path $full)) {
                    return [pscustomobject]@{ Path = $full; Version = $reported }
                }
            }
        } catch { }
    }
    return $null
}

Write-Step 'Checking for Python'
$found = Find-Python

if (-not $found) {
    if (Test-Command 'winget') {
        Write-Step 'Installing Python via winget'
        winget install --id Python.Python.3.12 --silent `
            --accept-source-agreements --accept-package-agreements
    } else {
        # No winget (common on Windows Server) -- install straight from python.org.
        $pyVersion = '3.12.7'
        $installer = Join-Path $env:TEMP "python-$pyVersion-amd64.exe"
        Write-Step "winget unavailable; downloading Python $pyVersion from python.org"
        Invoke-WebRequest -UseBasicParsing `
            -Uri "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-amd64.exe" `
            -OutFile $installer
        Write-Ok 'Downloaded. Installing silently (this takes a minute)...'
        $proc = Start-Process -FilePath $installer -Wait -PassThru -ArgumentList @(
            '/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_pip=1',
            'Include_test=0', 'Include_launcher=1'
        )
        # 3010 = success, reboot advised. Neither blocks us.
        if ($proc.ExitCode -notin @(0, 3010)) {
            throw "Python installer exited with code $($proc.ExitCode)."
        }
    }
    Refresh-Path
    $found = Find-Python
    if (-not $found) {
        throw 'Python installed but could not be located. Close this window, open a ' +
              'new Administrator PowerShell, and re-run this script.'
    }
}

$pythonExe = $found.Path
Write-Ok "Python $($found.Version) at $pythonExe"

# --- code -----------------------------------------------------------------
# Git is optional: it is only needed to update in place. If it is missing we
# refresh from the branch zip instead, which needs no extra software.
$hasGit = Test-Command 'git'

function Update-FromZip {
    Write-Step 'Refreshing code from GitHub (zip)'
    $zip     = Join-Path $env:TEMP 'fwr-update.zip'
    $extract = Join-Path $env:TEMP 'fwr-update'
    $url     = "https://github.com/flashtechusa/fantasy-war-room/archive/refs/heads/$Branch.zip"

    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip
    if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
    Expand-Archive $zip -DestinationPath $extract -Force

    $inner = (Get-ChildItem $extract -Directory | Select-Object -First 1).FullName
    # Copy everything except data\, so the league database is never clobbered.
    Get-ChildItem $inner -Force | Where-Object { $_.Name -ne 'data' } | ForEach-Object {
        Copy-Item $_.FullName -Destination $InstallDir -Recurse -Force
    }
    Write-Ok 'Code refreshed.'
}

if (Test-Path (Join-Path $InstallDir '.git')) {
    if ($hasGit) {
        Write-Step "Updating existing checkout at $InstallDir"
        Push-Location $InstallDir
        try {
            git fetch origin $Branch
            git checkout $Branch
            git pull --ff-only origin $Branch
        } finally { Pop-Location }
        Write-Ok 'Code is current.'
    } else {
        Update-FromZip
    }
} elseif (Test-Path (Join-Path $InstallDir 'pyproject.toml')) {
    # Code is already here (unpacked by hand) -- leave it alone.
    Write-Ok "Using the code already at $InstallDir."
} else {
    Update-FromZip
}

# --- python environment ---------------------------------------------------
Write-Step 'Building the virtual environment'
$venv   = Join-Path $InstallDir '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $python)) { & $pythonExe -m venv $venv }
if (-not (Test-Path $python)) { throw "Failed to create a virtualenv at $venv." }
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

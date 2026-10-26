# Keeps the Miniville observer running: one launcher per session (mutex), the
# server restarted 30 s after any exit. The launcher never wedges: it waits on
# the server with a bounded poll (Start-Process -PassThru), checks that port
# 8751 actually comes up, kills a hung-but-silent server (taskkill /T so the
# venv shim and its interpreter both die), and gives up — releasing the mutex —
# after 5 rapid exits. A dead server therefore never leaves a stale launcher
# blocking the scheduled task: a fresh task run can always take over.
# Serves the 10-foot TV director mode (--live-step 8 advances the town 1 tick
# every 8 s in the background). Run by the scheduled task
# \VaultWares\Miniville Observer (scripts\Register-ObserverTask.ps1).
# Tailnet-only: binds 100.71.101.21 (clopeux-desktop) so greencloud's nginx
# (miniville.vaultwares.ca) can proxy to it; never 0.0.0.0.
# Logs (under %LOCALAPPDATA%\VaultWares\miniville\): observer.out.log and
# observer.err.log hold the server's stdout/stderr for the current run
# (truncated on each start); launcher.log appends launcher events (start,
# kills, exits, give-up). Override the interpreter for testing via
# $env:MINIVILLE_OBSERVER_PYTHON.
$ErrorActionPreference = "Continue"
$created = $false
$mutex = New-Object System.Threading.Mutex($true, "Local\MinivilleObserver", [ref]$created)
if (-not $created) { exit 0 }
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if ($env:MINIVILLE_OBSERVER_PYTHON) { $python = $env:MINIVILLE_OBSERVER_PYTHON }
$env:PYTHONIOENCODING = "utf-8"
$logDir = Join-Path $env:LOCALAPPDATA "VaultWares\miniville"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir "observer.out.log"
$errLog = Join-Path $logDir "observer.err.log"
$launcherLog = Join-Path $logDir "launcher.log"
$port = 8751
$restartDelaySeconds = 30
$maxRapidExits = 5
$graceSeconds = 30

function Test-ObserverPort([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Write-LauncherLog([string]$Message) {
    $stamp = Get-Date -Format "ddd, dd MMM yyyy HH:mm"
    Add-Content -LiteralPath $launcherLog -Value "[$stamp] $Message"
}

try {
    $rapidExits = 0
    while ($true) {
        if (Test-ObserverPort $port) {
            # Someone (a rogue server, a manual run) holds the port: don't
            # fight, just wait until it clears, then supervise again.
            Write-LauncherLog "port $port already listening; waiting for it to clear"
            Start-Sleep -Seconds 10
            continue
        }
        $startedAt = Get-Date
        Push-Location -LiteralPath $root
        try {
            $proc = Start-Process -FilePath $python `
                -ArgumentList @('-u', '-m', 'miniville.cli', 'serve', '--host', '100.71.101.21', '--port', "$port", '--live-step', '8') `
                -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
        }
        catch {
            Write-LauncherLog "could not start server ($python): $($_.Exception.Message); giving up"
            Pop-Location
            exit 1
        }
        Pop-Location
        Write-LauncherLog "server started PID $($proc.Id)"

        # Bounded wait: poll so a dead or hung child can never wedge this loop.
        while (-not $proc.HasExited) {
            if ($proc.WaitForExit(2000)) { break }
            $upSeconds = ((Get-Date) - $startedAt).TotalSeconds
            if ($upSeconds -ge $graceSeconds -and -not (Test-ObserverPort $port)) {
                Write-LauncherLog "server PID $($proc.Id) alive but port $port not listening after $graceSeconds s; killing it"
                taskkill /PID $($proc.Id) /T /F | Out-Null
            }
        }

        $uptime = ((Get-Date) - $startedAt).TotalSeconds
        if ($uptime -lt $restartDelaySeconds) { $rapidExits++ } else { $rapidExits = 0 }
        if ($proc.HasExited) { $proc.WaitForExit() }  # flush async output so ExitCode is populated
        $exitCode = if ($proc.HasExited -and $null -ne $proc.ExitCode) { $proc.ExitCode } else { -1 }
        if ($rapidExits -ge $maxRapidExits) {
            Write-LauncherLog "server exited $rapidExits times in a row within ${restartDelaySeconds}s (last code $exitCode); giving up so a fresh launcher can take over"
            exit 1
        }
        Write-LauncherLog "server exited (code $exitCode); restarting in ${restartDelaySeconds}s"
        Start-Sleep -Seconds $restartDelaySeconds
    }
}
finally {
    $mutex.ReleaseMutex()
}
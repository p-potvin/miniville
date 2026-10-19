# Keeps the Miniville observer running: one launcher per session (mutex), the
# server restarted 30 s after any exit. Serves the 10-foot TV director mode
# (--live-step 8 advances the town 1 tick every 8 s in the background).
# Run by the scheduled task \VaultWares\Miniville Observer
# (scripts\Register-ObserverTask.ps1). Tailnet-only: binds 100.71.101.21
# (clopeux-desktop) so greencloud's nginx (miniville.vaultwares.ca) can proxy
# to it; never 0.0.0.0.
$ErrorActionPreference = "Continue"
$created = $false
$mutex = New-Object System.Threading.Mutex($true, "Local\MinivilleObserver", [ref]$created)
if (-not $created) { exit 0 }
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"
$logDir = Join-Path $env:LOCALAPPDATA "VaultWares\miniville"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "observer.log"
try {
    while ($true) {
        Push-Location -LiteralPath $root
        & $python -u -m miniville.cli serve --host 100.71.101.21 --port 8751 --live-step 8 *>> $log
        Pop-Location
        Start-Sleep -Seconds 30
    }
} finally {
    $mutex.ReleaseMutex()
}
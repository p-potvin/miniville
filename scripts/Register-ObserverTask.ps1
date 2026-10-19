# Registers \VaultWares\Miniville Observer: at logon, hidden
# (conhost --headless), never times out. Same model as vault-raggae's
# \VaultWares\Enrichment Gateway task.
$launcher = Join-Path $PSScriptRoot "Start-Observer.ps1"
$action = New-ScheduledTaskAction -Execute "conhost.exe" -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$launcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -Priority 7 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskPath "\VaultWares\" -TaskName "Miniville Observer" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Miniville town observer + 10-foot TV director mode. Serves miniville.vaultwares.ca via greencloud nginx proxy to 100.71.101.21:8751 (--live-step 8). Log %LOCALAPPDATA%\VaultWares\miniville\observer.log" -Force | Out-Null
"registered"
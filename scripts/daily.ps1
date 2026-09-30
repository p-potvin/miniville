# Miniville daily routine: advance N days, narrate (HF->Ollama->raw), snapshot.
# Usage: .\scripts\daily.ps1 [-Days 1] [-Provider hf] [-Write]
param(
    [int]$Days = 1,
    [ValidateSet("hf","ollama","raw")][string]$Provider = "hf",
    [switch]$Write
)
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo ".venv\Scripts\python.exe"
$argv = @("-m", "miniville.cli", "daily", "--days", $Days, "--provider", $Provider)
if ($Write) { $argv += "--write" }
& $py @argv
exit $LASTEXITCODE

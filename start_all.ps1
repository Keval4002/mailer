$ErrorActionPreference = "Stop"

# Use native $PSScriptRoot (or fallback to current directory if run interactively)
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { $PWD.Path }

Write-Host "Starting Mailtfoutofit Backend (Port 8000)..."
Start-Process "uv" -ArgumentList "run uvicorn mail_scheduler.app:app --host 127.0.0.1 --port 8000" -WorkingDirectory "$ScriptDir\mailtfoutofit"

Write-Host "Starting OpenOutreach Daemon (Port 9000)..."
Start-Process "uv" -ArgumentList "run uvicorn app:app --host 127.0.0.1 --port 9000" -WorkingDirectory "$ScriptDir\openoutreach_daemon"

Write-Host ""
Write-Host "All services started successfully in new windows!"
Write-Host "- Mailtfoutofit is running on http://127.0.0.1:8000"
Write-Host "- OpenOutreach Daemon is running on http://127.0.0.1:9000"
Write-Host ""
Write-Host "Close the spawned terminal windows to stop the services."

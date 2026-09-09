# dev.ps1 — Start backend + frontend for local development (Windows PowerShell)
$ErrorActionPreference = "Stop"
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { $PWD.Path }

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Personal Mail Scheduler — Dev Mode    " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check .env
if (-not (Test-Path "$ScriptDir\mailtfoutofit\.env")) {
    Write-Host "[backend] .env not found — copy .env.example and fill in secrets:" -ForegroundColor Red
    Write-Host "  Copy-Item mailtfoutofit\.env.example mailtfoutofit\.env" -ForegroundColor Yellow
    exit 1
}

# Auto-create .env.local from example if missing
if (-not (Test-Path "$ScriptDir\frontend-next\.env.local")) {
    Write-Host "[frontend] .env.local not found — creating from example..." -ForegroundColor Yellow
    Copy-Item "$ScriptDir\frontend-next\.env.local.example" "$ScriptDir\frontend-next\.env.local"
}

Write-Host "[backend]  Starting FastAPI  -> http://127.0.0.1:8000" -ForegroundColor Cyan
Write-Host "[frontend] Starting Next.js  -> http://localhost:3000" -ForegroundColor Green
Write-Host ""
Write-Host "Both services running. Close this window or press Ctrl+C to stop." -ForegroundColor White
Write-Host ""

# Start backend in background job
$backendJob = Start-Job -ScriptBlock {
    param($dir)
    Set-Location $dir
    uv run uvicorn mail_scheduler.app:app --host 127.0.0.1 --port 8000
} -ArgumentList "$ScriptDir\mailtfoutofit"

# Give backend a moment to bind
Start-Sleep -Seconds 2

# Start frontend in background job
$frontendJob = Start-Job -ScriptBlock {
    param($dir)
    Set-Location $dir
    $env:NEXT_PUBLIC_API_URL = "http://localhost:8000"
    node "C:\Program Files\nodejs\node_modules\npm\bin\npm-cli.js" run dev
} -ArgumentList "$ScriptDir\frontend-next"

# Stream output from both jobs until Ctrl+C
try {
    while ($true) {
        $backendOutput = Receive-Job $backendJob
        foreach ($line in $backendOutput) {
            Write-Host "[backend]  $line" -ForegroundColor Cyan
        }
        $frontendOutput = Receive-Job $frontendJob
        foreach ($line in $frontendOutput) {
            Write-Host "[frontend] $line" -ForegroundColor Green
        }
        Start-Sleep -Milliseconds 500
    }
} finally {
    Write-Host "`nShutting down..." -ForegroundColor Yellow
    Stop-Job $backendJob, $frontendJob -ErrorAction SilentlyContinue
    Remove-Job $backendJob, $frontendJob -Force -ErrorAction SilentlyContinue
    Write-Host "Done." -ForegroundColor Green
}
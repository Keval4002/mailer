# dev.ps1 - Start backend + frontend for local development (Windows PowerShell)
$ErrorActionPreference = "Stop"
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { $PWD.Path }

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Personal Mail Scheduler - Dev Mode    " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path "$ScriptDir\mailtfoutofit\.env")) {
    Write-Host "[backend] .env not found - copy .env.example and fill in secrets:" -ForegroundColor Red
    Write-Host "  Copy-Item mailtfoutofit\.env.example mailtfoutofit\.env" -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path "$ScriptDir\frontend-next\.env.local")) {
    Write-Host "[frontend] .env.local not found - creating from example..." -ForegroundColor Yellow
    Copy-Item "$ScriptDir\frontend-next\.env.local.example" "$ScriptDir\frontend-next\.env.local"
}

# Extract API token from backend .env and expose to frontend
$envContent = Get-Content "$ScriptDir\mailtfoutofit\.env"
$apiToken = ($envContent | Where-Object { $_ -match '^API_BEARER_TOKEN=' }) -replace '^API_BEARER_TOKEN=','' -replace '\r',''
if ($apiToken) {
    $env:NEXT_PUBLIC_API_TOKEN = $apiToken
    $env:NEXT_PUBLIC_API_URL   = "http://localhost:8000"
    Write-Host "[auth]     API token loaded from mailtfoutofit/.env" -ForegroundColor Cyan
}

Write-Host "[backend]  Starting FastAPI  -> http://127.0.0.1:8000" -ForegroundColor Cyan
Write-Host "[frontend] Starting Next.js  -> http://localhost:3000" -ForegroundColor Green
Write-Host ""
Write-Host "Both services running. Press Ctrl+C to stop." -ForegroundColor White
Write-Host ""

$backendJob = Start-Job -ScriptBlock {
    param($dir)
    Set-Location $dir
    uv run uvicorn mail_scheduler.app:app --host 127.0.0.1 --port 8000
} -ArgumentList "$ScriptDir\mailtfoutofit"

Start-Sleep -Seconds 2

$frontendJob = Start-Job -ScriptBlock {
    param($dir, $token, $url)
    Set-Location $dir
    $env:NEXT_PUBLIC_API_TOKEN = $token
    $env:NEXT_PUBLIC_API_URL   = $url
    node "C:\Program Files\nodejs\node_modules\npm\bin\npm-cli.js" run dev
} -ArgumentList "$ScriptDir\frontend-next", $apiToken, "http://localhost:8000"

try {
    while ($true) {
        $backendOutput = Receive-Job $backendJob
        foreach ($line in $backendOutput) { Write-Host "[backend]  $line" -ForegroundColor Cyan }
        $frontendOutput = Receive-Job $frontendJob
        foreach ($line in $frontendOutput) { Write-Host "[frontend] $line" -ForegroundColor Green }
        Start-Sleep -Milliseconds 500
    }
} finally {
    Write-Host "`nShutting down..." -ForegroundColor Yellow
    Stop-Job $backendJob, $frontendJob -ErrorAction SilentlyContinue
    Remove-Job $backendJob, $frontendJob -Force -ErrorAction SilentlyContinue
    Write-Host "Done." -ForegroundColor Green
}
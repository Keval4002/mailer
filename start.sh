#!/bin/bash
set -e

echo "=========================================="
echo "Starting Personal Mail Scheduler (Bash)"
echo "=========================================="

echo ""
echo "[1/2] Building React Frontend..."
cd frontend
npm run build
cd ..

echo ""
echo "[2/2] Starting FastAPI Server (Port 8000)..."
cd mailtfoutofit
uv run uvicorn mail_scheduler.app:app --host 127.0.0.1 --port 8000

#!/usr/bin/env bash
# dev.sh — Start backend + frontend for local development
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/mailtfoutofit"
FRONTEND="$ROOT/frontend-next"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; RESET='\033[0m'; BOLD='\033[1m'

cleanup() {
  echo -e "\n${YELLOW}Shutting down...${RESET}"
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
  wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
  echo -e "${GREEN}Done.${RESET}"
  exit 0
}
trap cleanup SIGINT SIGTERM

echo -e "${BOLD}========================================${RESET}"
echo -e "${BOLD}  Personal Mail Scheduler — Dev Mode    ${RESET}"
echo -e "${BOLD}========================================${RESET}"
echo ""

# Check .env exists
if [ ! -f "$BACKEND/.env" ]; then
  echo -e "${RED}[backend] .env not found. Copy and fill in secrets:${RESET}"
  echo -e "  cp mailtfoutofit/.env.example mailtfoutofit/.env"
  exit 1
fi

# Auto-create frontend .env.local if missing
if [ ! -f "$FRONTEND/.env.local" ]; then
  echo -e "${YELLOW}[frontend] .env.local not found — creating from example...${RESET}"
  cp "$FRONTEND/.env.local.example" "$FRONTEND/.env.local"
fi

# Extract API token from backend .env and expose to frontend
API_TOKEN=$(grep -E '^API_BEARER_TOKEN=' "$BACKEND/.env" | cut -d= -f2 | tr -d '\r')
if [ -n "$API_TOKEN" ]; then
  export NEXT_PUBLIC_API_TOKEN="$API_TOKEN"
  echo -e "${CYAN}[auth]${RESET}     API token loaded from mailtfoutofit/.env"
fi

export NEXT_PUBLIC_API_URL="http://localhost:8000"

echo -e "${CYAN}[backend]${RESET}  Starting FastAPI  → http://127.0.0.1:8000"
echo -e "${GREEN}[frontend]${RESET} Starting Next.js  → http://localhost:3000"
echo ""

# Start backend
(
  cd "$BACKEND"
  uv run uvicorn mail_scheduler.app:app --host 127.0.0.1 --port 8000 --reload 2>&1 \
    | sed "s/^/$(printf '\033[0;36m')[backend]$(printf '\033[0m')  /"
) &
BACKEND_PID=$!

sleep 1

# Start frontend (env vars are inherited from this shell)
(
  cd "$FRONTEND"
  npm run dev 2>&1 \
    | sed "s/^/$(printf '\033[0;32m')[frontend]$(printf '\033[0m') /"
) &
FRONTEND_PID=$!

echo -e "${BOLD}Both services running. Press Ctrl+C to stop.${RESET}"
echo ""

wait "$BACKEND_PID" "$FRONTEND_PID"
# Mailer

Personal outreach email scheduler with Gmail OAuth, follow-up sequences, and a campaign builder UI.

## Structure

```
mailer/
├── frontend-next/   # Next.js 16 app (deploys to Vercel)
└── mailtfoutofit/   # Python FastAPI backend (deploys to Cloudflare Workers)
```

## Frontend (Next.js)

```bash
cd frontend-next
cp .env.local.example .env.local   # set NEXT_PUBLIC_API_URL
npm install
npm run dev
```

### Deploy to Vercel
- Set root directory to `frontend-next`
- Add env var: `NEXT_PUBLIC_API_URL` = your backend URL

## Backend (FastAPI)

```bash
cd mailtfoutofit
cp .env.example .env               # fill in secrets
uv venv && uv pip install -r requirements.txt
uv run uvicorn mail_scheduler.app:app --host 127.0.0.1 --port 8000
```

### Deploy to Cloudflare Workers (Python)
```bash
cd mailtfoutofit
wrangler deploy
wrangler secret put API_BEARER_TOKEN
wrangler secret put GMAIL_CLIENT_ID
wrangler secret put GMAIL_CLIENT_SECRET
```

## Local Dev (Both Together)

```powershell
# Terminal 1 — Backend
cd mailtfoutofit
uv run uvicorn mail_scheduler.app:app --host 127.0.0.1 --port 8000

# Terminal 2 — Frontend
cd frontend-next
npm run dev
```
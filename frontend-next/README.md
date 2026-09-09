# Frontend — Personal Mail Scheduler

Next.js 16 app (App Router) for managing email campaigns, contacts, and notes.

## Setup

```bash
cp .env.local.example .env.local
# Edit .env.local and set NEXT_PUBLIC_API_URL to your backend URL

npm install
npm run dev      # http://localhost:3000
npm run build    # production build
```

## Pages

| Path | Description |
|------|-------------|
| `/` | Dashboard — Gmail status + stats |
| `/campaign-builder` | Create & schedule email sequences |
| `/contacts` | Contact directory |
| `/reminders` | Notes with Markdown support |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NEXT_PUBLIC_API_URL` | Backend API base URL | `http://localhost:8000` |

## Deploy to Vercel

1. Import the repo at vercel.com
2. Set **Root Directory** to `frontend-next`
3. Add `NEXT_PUBLIC_API_URL` env var in Vercel dashboard
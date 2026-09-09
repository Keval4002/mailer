# Personal AI-Controlled Mail Scheduler

FastAPI app for storing, reviewing, editing, and sending scheduled emails through Gmail API + OAuth, with thread-aware follow-ups and reply-based stop logic.

## Features

- Bearer-token protected REST API for local AI ingestion
- Minimal HTML dashboard for browsing and editing scheduled jobs
- Contacts directory with company/title/LinkedIn and mail history
- Gmail OAuth connect/disconnect flow in the backend
- Gmail-thread-aware follow-ups using `parent_job_id` / `root_job_id`
- Reply sync via Gmail polling/history so pending follow-ups can be blocked automatically
- SQLite persistence
- Resume/file uploads stored on the server
- Background scheduler that sends due jobs every minute

## Run locally

```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env
uv run uvicorn mail_scheduler.app:app --reload
```

## Main endpoints

- `GET /auth/gmail/start`
- `GET /auth/gmail/callback`
- `POST /auth/gmail/disconnect`
- `GET /auth/gmail/status`
- `POST /api/files`
- `GET /api/files`
- `GET /api/contacts`
- `GET /api/contacts/{id}`
- `POST /api/mail-jobs/bulk`
- `POST /api/outreach-workflows/bulk`
- `GET /api/outreach-workflows`
- `GET /api/outreach-workflows/{id}`
- `GET /api/linkedin-jobs`
- `GET /api/linkedin-jobs/{id}`
- `GET /api/mail-jobs`
- `GET /api/mail-jobs/{id}`
- `PATCH /api/mail-jobs/{id}`
- `POST /api/mail-jobs/{id}/send-now`
- `POST /api/mail-jobs/{id}/cancel`

## Gmail OAuth setup

1. Create a Google OAuth web app in Google Cloud Console.
2. Add a redirect URI for the server callback:
   - preferred: `https://your-host/auth/gmail/callback`
   - fallback for local tunnel testing: `http://127.0.0.1:8011/auth/gmail/callback`
3. Put `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REDIRECT_URI`, and `PUBLIC_BASE_URL` into `.env`.
4. Start the app and open `/auth/gmail/start`.
5. If Google rejects the public callback, use `/auth/gmail/start?mode=local` through your SSH tunnel instead.

The backend stores OAuth refresh/access tokens in SQLite, not in `.env`.

## Bulk mail payload

`POST /api/mail-jobs/bulk` still accepts root jobs, but now also supports explicit follow-up threading:

```json
{
  "batch_name": "amazon-apr18",
  "jobs": [
    {
      "recipient_email": "someone@example.com",
      "subject": "First touch",
      "body_text": "Hi ...",
      "scheduled_at": "2026-04-20T09:00:00+05:30",
      "attach_latest_resume": true
    },
    {
      "recipient_email": "someone@example.com",
      "subject": "Following up",
      "body_text": "Hi again ...",
      "scheduled_at": "2026-04-22T09:00:00+05:30",
      "parent_job_id": "first-job-id",
      "reply_stop_enabled": true
    }
  ]
}
```

Notes:

- `scheduled_at` must include a timezone offset.
- The backend still does not invent sequences or write email content for you.
- `parent_job_id` makes a job a follow-up in the same Gmail thread.
- If Gmail sync detects a reply, future pending follow-ups in that thread are marked `blocked`.

## Mixed outreach workflow payload

`POST /api/outreach-workflows/bulk` is the new mixed-channel ingestion path.

- The local/runtime agent still decides the sequence and writes the copy.
- The backend stores explicit email and LinkedIn steps.
- Gmail reply-stop and LinkedIn-connected skip logic are enforced by the scheduler.
- OpenOutreach remains the LinkedIn executor; this backend is the durable source of truth.

Minimal example:

```json
{
  "batch_name": "hackspire-apr26",
  "workflows": [
    {
      "client_workflow_id": "shubh-001",
      "contact": {
        "recipient_email": "founder@example.com",
        "recipient_name": "Shubh",
        "linkedin_url": "https://www.linkedin.com/in/example/"
      },
      "steps": [
        {
          "type": "email",
          "step_id": "email-1",
          "subject": "Quick note",
          "body_text": "Hi ...",
          "scheduled_at": "2026-04-26T10:00:00+05:30"
        },
        {
          "type": "linkedin",
          "kind": "connection_request",
          "step_id": "li-connect",
          "scheduled_at": "2026-04-26T14:00:00+05:30"
        },
        {
          "type": "linkedin",
          "kind": "message_after_acceptance",
          "step_id": "li-message",
          "message": "Thanks for connecting ...",
          "requires_linkedin_connected": true,
          "cancel_on_gmail_reply": true,
          "scheduled_at": "2026-04-28T12:00:00+05:30"
        }
      ]
    }
  ]
}
```

## Tests

```bash
uv run pytest
```

# Agent Notes

This file is for future agents working on this repo. It captures context that is easy to miss if you only read the code.

## GitHub and shell conventions

- Use the personal GitHub account, not a default/global GitHub auth context.
- The intended GitHub CLI alias is `gh-personal`.
- On this machine, `gh-personal` is a `zsh` alias from `~/.zshrc`, so it may not exist in non-interactive shells.
- If you need to use it from automation or one-off shell commands, prefer:
  - `zsh -ic 'gh-personal ...'`
  - or `GH_CONFIG_DIR=$HOME/.config/gh-personal ...` for raw `git`/`gh` operations
- Repo:
  - GitHub URL: `https://github.com/Utkarsh09102004/mailtfoutofit`
  - Default branch: `main`

## Server deployment setup

- Target server SSH host alias: `genserver`
- Server user: `utkarsh`
- Server app path:
  - `/home/utkarsh/serious-shit/mailtfoutofit`
- The app is deployed and managed with `pm2`.
- PM2 process name:
  - `mailtfoutofit`
- The FastAPI app currently runs on:
  - `127.0.0.1:8011` on the server
- It is publicly exposed through nginx at:
  - `http://mailtfoutofit.80.225.208.243.sslip.io/`
- Local fallback access still works through an SSH tunnel:
  - `ssh -L 8011:127.0.0.1:8011 genserver`
  - then open `http://127.0.0.1:8011/`

Useful server commands:

```bash
ssh genserver 'pm2 ls'
ssh genserver 'pm2 logs mailtfoutofit --lines 100'
ssh genserver 'curl http://127.0.0.1:8011/'
ssh genserver 'cd /home/utkarsh/serious-shit/mailtfoutofit && git rev-parse HEAD'
curl http://mailtfoutofit.80.225.208.243.sslip.io/
```

## Python/runtime setup

- The repo is standardized on `uv` + Python `3.11`.
- `.python-version` is committed and should be respected.
- Do not downgrade dependency versions just to match system Python on a box.
- If Python is missing on a machine, install with `uv python install 3.11`.
- Local setup:

```bash
uv python install 3.11
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
source .venv/bin/activate
pytest
```

- Server deploy setup uses the same approach.

## GitHub Actions auto-deploy

- GitHub Actions is configured to auto-deploy on push to `main`.
- Workflow file:
  - `.github/workflows/deploy.yml`
- Current behavior:
  1. checkout
  2. install `uv`
  3. install Python `3.11`
  4. create `.venv`
  5. install requirements
  6. run tests
  7. SSH into `genserver`
  8. pull latest code
  9. rebuild the venv with `uv`
  10. restart `pm2` on port `8011`

Configured GitHub repo secrets:

- `GENSERVER_HOST`
- `GENSERVER_USER`
- `GENSERVER_SSH_KEY`

If deploys fail, inspect:

```bash
zsh -ic 'gh-personal run list --repo Utkarsh09102004/mailtfoutofit --limit 5'
zsh -ic 'gh-personal run view <run-id> --repo Utkarsh09102004/mailtfoutofit --log-failed'
```

## API endpoints

The backend is intentionally simple. It is not an Apollo clone and it is not a backend-owned sequence engine.

Implemented API endpoints:

- `GET /auth/gmail/start`
  - starts Gmail OAuth using the configured public callback
- `GET /auth/gmail/callback`
  - stores Gmail OAuth tokens in SQLite
- `POST /auth/gmail/disconnect`
  - disconnects the saved Gmail account from the backend
- `GET /auth/gmail/status`
  - reports Gmail connection/token/sync status
- `POST /api/files`
  - multipart upload
  - used for server-side files like resume PDFs
- `GET /api/files`
  - list uploaded files
- `POST /api/mail-jobs/bulk`
  - main ingestion endpoint
  - creates one or more scheduled mail jobs
- `GET /api/mail-jobs`
  - list/filter jobs
- `GET /api/mail-jobs/{id}`
  - job detail
- `PATCH /api/mail-jobs/{id}`
  - edit pending/failed jobs
- `POST /api/mail-jobs/{id}/send-now`
  - immediate send
- `POST /api/mail-jobs/{id}/cancel`
  - cancel pending job

Implemented HTML routes:

- `/`
- `/jobs/{id}`
- `/files`

## How the API is meant to be used

Auth:

- API uses a bearer token from `.env`:
  - `API_BEARER_TOKEN`
- Do not hardcode or commit the actual token.

Main ingestion flow:

1. Local AI finds/selects contacts
2. Local AI decides the mail content and timing
3. Local AI either:
   - sends email-only jobs to `POST /api/mail-jobs/bulk`, or
   - sends mixed email + LinkedIn plans to `POST /api/outreach-workflows/bulk`
4. Server stores them, displays them, and sends them later
5. Gmail inbox sync can block pending follow-ups after replies
6. OpenOutreach executes LinkedIn work; this backend remains the source of truth

The key payload shape for `POST /api/mail-jobs/bulk` is:

```json
{
  "batch_name": "amazon-university-apr18",
  "jobs": [
    {
      "client_job_id": "root",
      "recipient_email": "someone@example.com",
      "recipient_name": "Someone",
      "company": "Amazon",
      "title": "Recruiter",
      "linkedin_url": "https://linkedin.com/in/example",
      "subject": "Application for role",
      "body_text": "Hi ...",
      "body_html": "<p>Hi ...</p>",
      "scheduled_at": "2026-04-20T09:00:00+05:30",
      "attach_latest_resume": true,
      "parent_job_id": null,
      "parent_job_client_id": null,
      "root_job_id": null,
      "reply_stop_enabled": true
    }
  ]
}
```

Important:

- `scheduled_at` must include an explicit timezone offset
- backend stores UTC but the UI should show readable human time
- backend sends final content exactly as provided
- no backend templating/sequence derivation is intended for v1
- `parent_job_id` turns a job into a same-thread Gmail follow-up when the parent already exists on the server
- `client_job_id` and `parent_job_client_id` are the preferred way to create a whole thread in one bulk request
- `root_job_id` is optional; if omitted for follow-ups the backend derives it from the parent
- `reply_stop_enabled=true` means future pending follow-ups in that thread can be auto-blocked after a reply

### Mixed workflow ingestion

Use `POST /api/outreach-workflows/bulk` when the runtime agent wants explicit
cross-channel steps such as:

- email 1
- LinkedIn connection request
- email 2
- LinkedIn message after acceptance
- email 3 that should be skipped once LinkedIn connects

The runtime agent still chooses the sequence and writes the copy. The backend
only stores explicit steps and enforces fixed guards such as:

- `cancel_on_gmail_reply`
- `cancel_on_linkedin_connected`
- `requires_linkedin_connected`

## Threading and bulk mode examples

There are 2 valid ways to build Gmail threads:

1. Reference an existing server-side parent with `parent_job_id`
2. Create the entire chain in one `POST /api/mail-jobs/bulk` call with request-local aliases:
   - `client_job_id`
   - `parent_job_client_id`

Use option 2 unless there is a specific reason to split creation across requests.

### Preferred one-call bulk thread example

This creates a 3-mail thread in one API call:

```json
{
  "batch_name": "live-thread-example",
  "jobs": [
    {
      "client_job_id": "root",
      "recipient_email": "someone@example.com",
      "recipient_name": "Someone",
      "subject": "Mail 1/3",
      "body_text": "First message",
      "scheduled_at": "2026-04-20T09:00:00+05:30",
      "attach_latest_resume": false,
      "reply_stop_enabled": true
    },
    {
      "client_job_id": "second",
      "recipient_email": "someone@example.com",
      "recipient_name": "Someone",
      "subject": "Mail 2/3",
      "body_text": "Second message",
      "scheduled_at": "2026-04-20T09:01:00+05:30",
      "attach_latest_resume": false,
      "parent_job_client_id": "root",
      "reply_stop_enabled": true
    },
    {
      "client_job_id": "third",
      "recipient_email": "someone@example.com",
      "recipient_name": "Someone",
      "subject": "Mail 3/3",
      "body_text": "Third message",
      "scheduled_at": "2026-04-20T09:02:00+05:30",
      "attach_latest_resume": false,
      "parent_job_client_id": "second",
      "reply_stop_enabled": true
    }
  ]
}
```

Expected behavior:

- job 1 becomes the thread root
- job 2 replies to job 1
- job 3 replies to job 2
- Gmail should assign the same `gmail_thread_id` to all sent jobs
- if the contact replies inside this thread before a later follow-up sends, the later pending follow-ups should become `blocked`

### Existing-parent follow-up example

Use this only when the parent job is already created and you want to add a new follow-up later:

```json
{
  "jobs": [
    {
      "recipient_email": "someone@example.com",
      "subject": "Follow up",
      "body_text": "Reply in the existing thread",
      "scheduled_at": "2026-04-21T09:00:00+05:30",
      "attach_latest_resume": false,
      "parent_job_id": "existing-job-uuid",
      "reply_stop_enabled": true
    }
  ]
}
```

### Live curl example

```bash
TOKEN='replace-me'
BASE='http://mailtfoutofit.80.225.208.243.sslip.io'

curl -X POST "$BASE/api/mail-jobs/bulk" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "batch_name": "demo-thread",
    "jobs": [
      {
        "client_job_id": "root",
        "recipient_email": "someone@example.com",
        "subject": "Thread 1/3",
        "body_text": "Root body",
        "scheduled_at": "2026-04-20T09:00:00+05:30",
        "attach_latest_resume": false,
        "reply_stop_enabled": true
      },
      {
        "client_job_id": "second",
        "recipient_email": "someone@example.com",
        "subject": "Thread 2/3",
        "body_text": "Second body",
        "scheduled_at": "2026-04-20T09:01:00+05:30",
        "attach_latest_resume": false,
        "parent_job_client_id": "root",
        "reply_stop_enabled": true
      },
      {
        "client_job_id": "third",
        "recipient_email": "someone@example.com",
        "subject": "Thread 3/3",
        "body_text": "Third body",
        "scheduled_at": "2026-04-20T09:02:00+05:30",
        "attach_latest_resume": false,
        "parent_job_client_id": "second",
        "reply_stop_enabled": true
      }
    ]
  }'
```

### Send-now example for debugging

If you need to send created jobs manually instead of waiting for the scheduler:

```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/mail-jobs/<job-id>/send-now"
```

### Things future agents must know about threads

- The backend generates/stores outbound `message_id` values and uses them for Gmail reply threading.
- Same-thread follow-ups rely on:
  - `In-Reply-To`
  - `References`
  - the latest sent ancestor in the chain
- Reply blocking is now scoped to the current thread root, not the entire contact forever.
- An old reply on the same email address must not block a brand-new root thread.
- A reply inside the current thread should block only future pending jobs in that same root chain.
- The scheduler now syncs inbox replies before processing due jobs.
- `send-now` for a follow-up also forces a reply sync first, to reduce race conditions.
- If a job is unexpectedly `blocked`, inspect:
  - the job detail
  - `blocked_reason`
  - `contact_replied_at`
  - `inbound_messages`
  - `gmail_thread_id`

## Gmail OAuth and reply sync

- Runtime sending is no longer SMTP-first; the active provider is Gmail API.
- OAuth tokens are stored in SQLite table `gmail_auth_state`, not in `.env`.
- `.env` now needs:
  - `MAIL_PROVIDER=gmail_api`
  - `MAIL_FROM`
  - `PUBLIC_BASE_URL`
  - `GMAIL_CLIENT_ID`
  - `GMAIL_CLIENT_SECRET`
  - `GMAIL_REDIRECT_URI`
  - `GMAIL_POLL_SECONDS`
  - `GMAIL_WATCH_ENABLED`
- Preferred auth flow:
  - hit `/auth/gmail/start`
  - complete the public callback flow
- Fallback if Google rejects the public-IP redirect:
  - use the SSH tunnel and `/auth/gmail/start?mode=local`
  - the app will use the local callback URL shape instead
- Reply sync currently uses Gmail polling/history checkpoints, not Pub/Sub watch yet.
- Pending follow-ups can auto-transition to `blocked` with `blocked_reason=contact_replied`.
- Outbound jobs now store:
  - `message_id`
  - `gmail_message_id`
  - `gmail_thread_id`
  - `parent_job_id`
  - `root_job_id`
  - `reply_stop_enabled`
  - `blocked_reason`
- Inbound reply matching is stored in `inbound_messages`.
- If Gmail says a contact replied, the backend should only block jobs that share the same `root_job_id`.

## Product vision from the user

This part matters a lot.

The user does **not** want the server to own campaign logic or sequence logic.

The intended split is:

- Local AI:
  - search Apollo
  - choose contacts
  - decide day 1/day 2/day 3 behavior
  - generate the exact email content
  - decide when each email should go out
- Genserver backend:
  - store files
  - store mail jobs
  - display mail jobs clearly
  - allow edit/cancel/send-now
  - send emails at scheduled time

The backend should stay dumb/simple on purpose.

Avoid turning this into:

- a CRM
- a sequence rules engine
- an analytics platform
- an AI personalization backend
- an Apollo replacement

For this project, the server is a personal mail executor and dashboard.

## Important domain assumptions

- Single-user tool
- Single Gmail account for send + read
- Latest uploaded active resume may be attached by the server when requested
- `batch_name` is just a lightweight grouping label from the local AI side
- It is not a true campaign/sequence model

## Things to avoid

- Do not commit `.env`
- Do not commit real tokens, app passwords, or SSH private keys
- Do not remove `uv`/Python 3.11 standardization just to match a machine’s system Python
- Do not assume public internet access to the app unless nginx/firewall setup is added later
- Do not revert the backend back to SMTP unless the user explicitly asks for that rollback

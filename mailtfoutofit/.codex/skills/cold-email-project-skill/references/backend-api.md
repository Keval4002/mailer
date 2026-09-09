# Backend API Notes

## Base URLs

Prefer this order unless the user explicitly wants the tunnel path:

- Public app: `http://mailtfoutofit.80.225.208.243.sslip.io`
- Local tunnel: `http://127.0.0.1:8011`

Set either through:

- `MAIL_BASE_URL`
- or the runtime script constant

## Auth

Use bearer auth on all API requests:

```http
Authorization: Bearer <API_BEARER_TOKEN>
```

The token belongs in env, not in committed code.

## Apollo Runtime Access

- `APOLLO_API_KEY` is available in `.env` on this machine and may be used by runtime scripts.
- Prefer direct Apollo HTTP API calls from runtime scripts.
- Use `mixed_people/api_search` for shortlist discovery and `people/match` for reveal/enrichment.
- It is valid to use Apollo contact search/export flows to pull already-enriched contacts before building scheduler payloads.
- Keep Apollo-derived enrichment fields local to the script unless they are part of the scheduler's supported job schema.
- Show the shortlist before enrichment when credits matter.
- After enrichment, dedupe by normalized email and skip already-sent contacts using `GET /api/contacts?email=...`.
- Include `linkedin_url` in the scheduler payload when available so the server persists that metadata on the contact row.

## Main Endpoints

### `GET /api/files`

Use before any send with `attach_latest_resume=true`.

Success shape:

```json
{
  "files": [
    {
      "id": "uuid",
      "kind": "resume",
      "original_name": "resume.pdf",
      "is_active": true
    }
  ]
}
```

### `POST /api/mail-jobs/bulk`

Creates one or more mail jobs.

Basic request shape:

```json
{
  "batch_name": "campus-apr20",
  "jobs": [
    {
      "recipient_email": "someone@example.com",
      "recipient_name": "Someone Example",
      "company": "Amazon",
      "title": "Recruiter",
      "linkedin_url": "https://linkedin.com/in/example",
      "subject": "Hello",
      "body_text": "Hi ...",
      "scheduled_at": "2026-04-20T09:00:00+05:30",
      "attach_latest_resume": true
    }
  ]
}
```

Success response:

```json
{
  "created_count": 1,
  "job_ids": ["uuid"]
}
```

### `POST /api/outreach-workflows/bulk`

Creates mixed email + LinkedIn workflow runs.

Use this when the runtime agent wants cross-channel timing and skip logic, for
example:

- email 1
- LinkedIn connection request
- email 2
- LinkedIn message after acceptance
- email 3 that should be skipped once LinkedIn connects

Basic request shape:

```json
{
  "batch_name": "founder-apr26",
  "workflows": [
    {
      "client_workflow_id": "shubh-001",
      "contact": {
        "recipient_email": "optional@example.com",
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

### `POST /api/mail-jobs/{id}/send-now`

Immediately attempts delivery for a pending or failed job.

Useful after creating a one-item bulk job when the user wants an immediate send.

### `GET /api/mail-jobs`

Useful filters:

- `status`
- `email`
- `name`
- `company`
- `title`
- `batch_name`
- `from_value`
- `to_value`

### `GET /api/mail-jobs/{id}`

Use to inspect one job in detail after scheduling or sending.

### `GET /api/contacts`

Useful filters:

- `company`
- `company_mode=any|present|missing`
- `title`
- `email`
- `name`
- `country`
- `has_linkedin=true|false`
- `has_sent_mail=true|false`

### `GET /api/contacts/{id}`

Returns the contact plus mail history. Useful for verifying what the server has scheduled or sent for one recipient.

## Allowed Job Fields

`POST /api/mail-jobs/bulk` is strict. The per-job allowlist is:

- `recipient_email`
- `recipient_name`
- `company`
- `title`
- `linkedin_url`
- `subject`
- `body_text`
- `body_html`
- `scheduled_at`
- `attach_latest_resume`
- `client_job_id`
- `parent_job_id`
- `parent_job_client_id`
- `root_job_id`
- `reply_stop_enabled`

Anything else can cause a `422`.

## Contact Identity Rule

The backend prefers these identities in order:

- normalized email
- Apollo person id
- normalized LinkedIn URL

So LinkedIn-only contacts are valid for mixed workflows.

Apollo-style fields like `first_name`, `city`, `seniority`, `company_domain` are fine for local rendering, but should usually be projected out before POSTing unless the API explicitly supports them.

## Threading

### New thread created entirely in one bulk request

Use request-local aliases:

```json
{
  "batch_name": "amazon-thread-apr19",
  "jobs": [
    {
      "client_job_id": "root",
      "recipient_email": "someone@example.com",
      "subject": "Email 1",
      "body_text": "Body 1",
      "scheduled_at": "2026-04-19T19:00:00+05:30",
      "attach_latest_resume": false
    },
    {
      "client_job_id": "follow-1",
      "parent_job_client_id": "root",
      "recipient_email": "someone@example.com",
      "subject": "Email 2",
      "body_text": "Body 2",
      "scheduled_at": "2026-04-19T19:01:00+05:30",
      "attach_latest_resume": false,
      "reply_stop_enabled": true
    },
    {
      "client_job_id": "follow-2",
      "parent_job_client_id": "follow-1",
      "recipient_email": "someone@example.com",
      "subject": "Email 3",
      "body_text": "Body 3",
      "scheduled_at": "2026-04-19T19:02:00+05:30",
      "attach_latest_resume": false,
      "reply_stop_enabled": true
    }
  ]
}
```

### Follow-up to an already-existing job

Use `parent_job_id`:

```json
{
  "jobs": [
    {
      "recipient_email": "someone@example.com",
      "subject": "Follow up on existing thread",
      "body_text": "Following up here.",
      "scheduled_at": "2026-04-20T10:00:00+05:30",
      "attach_latest_resume": false,
      "parent_job_id": "existing-job-uuid",
      "reply_stop_enabled": true
    }
  ]
}
```

## Reply-Stop Behavior

- Set `reply_stop_enabled=true` on follow-ups.
- The backend can block later pending jobs in the same root thread if the recipient replies.
- This is thread-scoped, not contact-global forever.
- Verify blocked status through `GET /api/mail-jobs?batch_name=...`.

## Recommended Verification Flow

After creating jobs:

1. Call `GET /api/mail-jobs?batch_name=...`
2. Confirm all jobs were created with the right schedule
3. For immediate sends, inspect `gmail_thread_id`, `message_id`, and `status`
4. For reply-sensitive threads, confirm later jobs become `blocked` if the recipient replies

## General Script Rule

Keep the runtime script generic.

It should only:

- load data
- normalize names / convenience fields
- render placeholders or call template helpers
- build allowed payloads
- chunk when needed
- submit to `/api/mail-jobs/bulk`
- verify results

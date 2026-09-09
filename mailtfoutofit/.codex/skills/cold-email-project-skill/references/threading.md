# Threading Notes

## Why This Exists

The scheduler supports same-thread follow-ups, but future agents should not have to rediscover which fields create a thread and which fields are only for existing jobs.

## Two Ways To Build A Thread

### 1. Entire thread in one bulk request

Use:

- `client_job_id`
- `parent_job_client_id`

This is the preferred pattern for a new 2-step or 3-step sequence being created from scratch.

Example:

```json
{
  "batch_name": "new-thread",
  "jobs": [
    {
      "client_job_id": "root",
      "recipient_email": "someone@example.com",
      "subject": "First email",
      "body_text": "Body 1",
      "scheduled_at": "2026-04-19T19:00:00+05:30",
      "attach_latest_resume": false
    },
    {
      "client_job_id": "second",
      "parent_job_client_id": "root",
      "recipient_email": "someone@example.com",
      "subject": "Second email",
      "body_text": "Body 2",
      "scheduled_at": "2026-04-19T19:01:00+05:30",
      "attach_latest_resume": false,
      "reply_stop_enabled": true
    }
  ]
}
```

### 2. Follow-up to an already-existing job

Use:

- `parent_job_id`

This is for when the parent job already exists on the server and is not being created in the same request.

Example:

```json
{
  "jobs": [
    {
      "recipient_email": "someone@example.com",
      "subject": "Follow-up on saved thread",
      "body_text": "Checking in.",
      "scheduled_at": "2026-04-20T10:00:00+05:30",
      "attach_latest_resume": false,
      "parent_job_id": "existing-job-uuid",
      "reply_stop_enabled": true
    }
  ]
}
```

## Recommended Defaults

- root email:
  - no parent field
- follow-ups:
  - `reply_stop_enabled=true`
- new thread creation:
  - prefer one bulk request over multiple separate calls

## What The Backend Does

- Stores thread relationships via parent/root job ids
- Sends follow-ups in the same Gmail thread
- Can block later pending follow-ups when a reply is detected

## Reply-Stop Semantics

Important rule:

- reply blocking is scoped to the current root thread
- an old reply in a different thread should not block a brand-new root email to the same person

## Verification

After scheduling:

1. Check `GET /api/mail-jobs?batch_name=...`
2. Confirm root/follow-up job relationships
3. After sending, inspect `gmail_thread_id`
4. If the recipient replies, verify later pending jobs become:
   - `status = blocked`
   - `blocked_reason = contact_replied`

## Common Mistakes

- Using `parent_job_client_id` when the parent job already exists on the server
- Sending a thread as multiple unrelated root jobs
- Forgetting `reply_stop_enabled` on follow-ups
- Posting extra unsupported fields to `/api/mail-jobs/bulk`

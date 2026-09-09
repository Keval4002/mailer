---
name: cold-email-project-skill
description: Project-level skill for sending or scheduling cold emails through this repo's FastAPI mail scheduler. Use when Codex needs to draft email copy, reuse local mail template functions, create one-off sends or bulk batches, schedule same-thread follow-ups in one bulk API call, or verify reply-stop behavior through the scheduler API.
---

# Cold Email Project Skill

## When To Use This Skill

Use this skill whenever the task is about sending or scheduling email from this repo, including:

- one manual email to one person
- one future scheduled email
- a 2- or 3-step follow-up thread
- a bulk batch from JSON / CSV / Apollo-style rows
- reusing `mail_templates/` functions for subject/body generation
- verifying that jobs were created, sent, blocked, or attached to the right contact

## What This Repo Does

This repo is a personal Gmail-backed mail scheduler.

- The backend stores contacts and mail jobs.
- Emails are created through `POST /api/mail-jobs/bulk`.
- Mixed email + LinkedIn workflows are created through `POST /api/outreach-workflows/bulk`.
- Same-thread follow-ups are supported.
- Reply-stop is supported: later follow-ups in the same thread can be auto-blocked if the recipient replies.
- LinkedIn work is orchestrated here but executed remotely by OpenOutreach.
- Resume attachment is supported through the server-side "latest active resume" file.

This skill is a reference and workflow guide. The agent should usually write a fresh runtime script or make direct API calls for the current task.

Important execution note:

- a separate script file is not required for every task
- unless persistence or reuse is helpful, the agent can run the flow inline from bash using the environment's Python, for example with `python3 - <<'PY'`
- the reusable part should live in `mail_templates/`; the execution wrapper itself can stay ephemeral

## Deployment Facts

Prefer this order unless the user says otherwise:

1. Public server URL: `http://mailtfoutofit.80.225.208.243.sslip.io`
2. Local tunnel URL: `http://127.0.0.1:8011`

Auth is always:

```http
Authorization: Bearer <API_BEARER_TOKEN>
```

Read the token from env. Never hardcode it into committed files.

## Apollo Access

Apollo is available for runtime scripting in this repo.

- `APOLLO_API_KEY` is present in `.env` on this machine and can be read by one-off or reusable scripts.
- Prefer direct Apollo HTTP API calls from runtime scripts when Apollo access is needed.
- It is valid to use that key for pulling already-enriched Apollo contacts and for shortlisting / enriching new Apollo people when building outreach batches.
- Prefer reading the key from env at runtime rather than hardcoding it anywhere.
- Apollo-derived rows can be used for local placeholder rendering, filtering, and deduping before projecting down to the scheduler's allowed job fields.
- See `references/apollo-usage.md` for the tested search / enrich workflow.

## Core Rule

The agent writes the final outreach logic at runtime.

- Unless the user explicitly specifies otherwise, use the generic template pack under `mail_templates/generic_full_time_opportunity_mail/`.
- Unless the user explicitly asks for throwaway inline copy, the agent should create or update a reusable Python template module under `mail_templates/` first.
- Then the runtime script should import `build_email(...)` from that template module and use it to generate subject/body.
- If the repo already has a matching template pack under `mail_templates/`, reuse it instead of creating a new one.
- Only write subject/body inline in the runtime script when the user explicitly wants a one-off script or says not to create a reusable template.
- Before scheduling or sending any emails, show the final mail content to the user and get confirmation.
- The skill files are references; do not edit them for one-off sends.

## Formatting Preferences

These are repo-specific user preferences and should be treated as defaults unless the user explicitly asks for something else.

- Prefer sending both `body_text` and `body_html`.
- Use `body_html` for polished rendering:
  - clickable links
  - selective bold emphasis
  - clean paragraph spacing
- Do not dump raw URLs inline when the visible link text can be cleaner.

Default link style preferences:

- Profile/footer links should be an inline row:
  - `LinkedIn | GitHub | LeetCode`
- Project/product links should usually be attached to the project names inside the actual paragraph, not moved into the footer.
  - Good: `Fooddle`, `GlassFactory`, `Thapar University Timetable Portal` as clickable inline links in the project paragraph
  - Bad: adding those project links as a second footer row unless the user asks for that
- Avoid awkward label duplication such as:
  - `LinkedIn: LinkedIn`
  - `LeetCode: LeetCode`

Default bolding preferences:

- Bold only the highest-signal achievements and CTA phrases
- Good candidates for bold:
  - major quantified outcomes
  - company names when central to the ask
  - standout wins/awards
  - the main CTA line
- Do not over-bold entire paragraphs

Signature preferences for the current generic template:

- Keep phone number in the signature line
- Do not include a redundant plain email line in the signature
- Keep profile links below the signature as clickable inline links

## First Files To Read

Read these in order:

1. `references/backend-api.md`
2. `references/reference_script.py`
3. `references/apollo-usage.md` if the task involves Apollo sourcing or enrichment
4. `references/threading.md` if the task involves follow-ups or reply-stop
5. `references/template-usage.md` if the task should reuse `mail_templates/`

## Common Playbooks

### 1. Send One Email Now

Use when the user wants one message to one person.

- By default, create a small template module under `mail_templates/` and import it into a tiny runtime script or direct API call helper.
- A dedicated script file is optional; an inline bash + Python heredoc is fine.
- If the user explicitly wants one-off inline text, skip template creation.
- Build a `jobs` list with one item.
- Post to `POST /api/mail-jobs/bulk`.
- If immediate send is needed, follow with `POST /api/mail-jobs/{id}/send-now`.

### 2. Schedule One Future Email

- Build one job with timezone-aware `scheduled_at`.
- Default to IST when the user did not specify a timezone.
- Verify with `GET /api/mail-jobs?batch_name=...`.

### 3. Schedule A Same-Thread Follow-Up Sequence

Use one bulk API call.

- Unless the user explicitly specifies a different cadence, create:
  - first email at `T`
  - follow-up 1 at `T+2 days`
  - follow-up 2 at `T+5 days`
- In a single request, assign request-local aliases with `client_job_id`.
- Link children with `parent_job_client_id`.
- Set `reply_stop_enabled=true` on follow-ups.

This is the default shape for new 2- or 3-step sequences.

### 4. Follow Up On An Existing Thread

Use when the first email already exists on the server.

- Use `parent_job_id=<existing_job_id>`.
- Optionally pass `reply_stop_enabled=true`.
- Do not use `parent_job_client_id` unless the parent is also being created in the same request.

### 5. Bulk Scheduling From Structured Data

Supported sources:

- one manual dict
- JSON list
- CSV rows
- Apollo-style exported/enriched rows

The full row can be used for placeholder rendering, but only allowed scheduler fields may be posted to the backend.

### 6. Mixed Email + LinkedIn Workflow

Use `POST /api/outreach-workflows/bulk` when the runtime agent wants to combine:

- scheduled emails
- LinkedIn connection requests
- LinkedIn messages after connection acceptance
- fixed guard rules like `cancel_on_gmail_reply` or `cancel_on_linkedin_connected`

The runtime agent still decides the sequence and copy. The backend only stores
explicit steps and enforces those fixed guards over time.

## Template Packs

Look under `mail_templates/` before inventing new copy.

Default behavior:

- use `mail_templates/generic_full_time_opportunity_mail/` unless the user explicitly wants a different template
- reuse an existing template pack if it fits
- otherwise create a new reusable module or pack under `mail_templates/`
- then import `build_email(...)` from that module in the runtime script

Current pack:

- `mail_templates/generic_full_time_opportunity_mail/first_email.py`
- `mail_templates/generic_full_time_opportunity_mail/follow_up_1.py`
- `mail_templates/generic_full_time_opportunity_mail/follow_up_2.py`

Current convention:

- `render_subject(...)`
- `render_email(...)`
- `render_email_html(...)`
- `build_email(...) -> {"subject": ..., "body": ..., "body_html": ...}`

When a pack matches the outreach, import and call `build_email(...)` in the runtime script instead of duplicating the text.

When no pack matches, create one first and then import it. Do not default to embedding the whole mail body into the runtime script unless the user specifically wants that.

See `references/template-usage.md`.

## Threading Rules

Same-thread scheduling depends on the job graph, not on guessing subject lines.

- New thread created entirely in one request:
  - use `client_job_id`
  - use `parent_job_client_id`
- Follow-up to an existing saved job:
  - use `parent_job_id`

Important:

- `reply_stop_enabled` should usually be `true` for follow-ups
- reply-stop is scoped to the current root thread, not to the contact forever
- verification should check the job statuses after the reply arrives

See `references/threading.md`.

## Golden Workflow

For most tasks, follow this order:

1. Read `references/backend-api.md`.
2. Default to `mail_templates/generic_full_time_opportunity_mail/` unless the user asked for a different template.
3. If no pack matches and the user did not ask for throwaway inline text, create a new template module under `mail_templates/`.
4. Read `references/reference_script.py` before writing the runtime script.
5. Preflight resume with `GET /api/files` if any job uses `attach_latest_resume=true`.
6. Write a fresh runtime script or direct API call for the specific request, importing the template module.
   An ephemeral bash-invoked Python snippet is fine; a saved script file is not required unless it helps reuse or debugging.
7. If the user asked for a sequence and did not specify timing, default to `T`, `T+2 days`, and `T+5 days`.
8. Show the rendered mail content to the user and confirm before scheduling or sending.
9. Post the jobs through `POST /api/mail-jobs/bulk`, or use `POST /api/outreach-workflows/bulk` for mixed email + LinkedIn plans.
10. Verify with `GET /api/mail-jobs?batch_name=...`.
11. If needed, inspect the contact with `GET /api/contacts/{id}`.

## Safety Rules

- Every `scheduled_at` must be timezone-aware.
- Default to IST (`+05:30`) if the user did not specify otherwise.
- Default follow-up timing to `T+2 days` and `T+5 days` unless the user explicitly requests another cadence.
- Use `attach_latest_resume=true` only after confirming an active resume exists.
- Do not send extra Apollo-only fields to `/api/mail-jobs/bulk`; render with them locally, then project down.
- Prefer chunking for large batches.
- For same-thread sequences, prefer one bulk request over separate API calls.
- Before any send or scheduling action, confirm the final mail content with the user.
- After a reply-sensitive send, verify whether later jobs became `blocked`.

## Files In This Skill

- `references/backend-api.md`
  Current live API contract, base URLs, endpoints, and field-level rules.
- `references/reference_script.py`
  Canonical Python cookbook for auth, scheduling, payload projection, bulk posting, template loading, and verification.
- `references/threading.md`
  Same-thread bulk scheduling, alias fields, and reply-stop behavior.
- `references/template-usage.md`
  How to inspect and reuse `mail_templates/` packs from runtime scripts.
- `references/apollo-usage.md`
  Direct Apollo API workflow, credit-aware shortlisting, enrichment, deduping, and scheduler projection rules.
- `agents/openai.yaml`
  UI metadata for this skill.

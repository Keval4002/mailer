# Template Usage Notes

## Goal

Teach future agents how to reuse local Python template packs under `mail_templates/` instead of rewriting known email copy.

Default policy:

- unless the user explicitly says otherwise, use `mail_templates/generic_full_time_opportunity_mail/`
- unless the user explicitly asks for one-off inline copy, create or update a reusable template module under `mail_templates/`
- then import that module's `build_email(...)` into the runtime script
- the execution wrapper does not need to be a saved script file; an inline bash + Python heredoc is fine unless a persistent script is actually useful

## Current Template Directory

Current repo-local pack:

- `mail_templates/generic_full_time_opportunity_mail/first_email.py`
- `mail_templates/generic_full_time_opportunity_mail/follow_up_1.py`
- `mail_templates/generic_full_time_opportunity_mail/follow_up_2.py`

## Current Function Convention

Each module currently exposes:

- `render_subject(...)`
- `render_email(...)`
- `render_email_html(...)`
- `build_email(...)`

Recommended setup:

- `render_email(...)` should remain the clean plain-text fallback
- `render_email_html(...)` should carry the polished formatting
- `build_email(...)` should return:
  - `subject`
  - `body`
  - `body_html`

The runtime script should prefer:

```python
from mail_templates.generic_full_time_opportunity_mail.first_email import build_email

payload = build_email(first_name="Ananya", company="Amazon")
subject = payload["subject"]
body = payload["body"]
body_html = payload["body_html"]
```

## User Formatting Preferences To Preserve

When creating or editing templates in this repo, preserve these defaults unless the user explicitly asks otherwise:

- send `body_html` whenever possible
- use selective `<strong>` emphasis only for the most relevant lines or quantified outcomes
- footer/profile links should look like:
  - `LinkedIn | GitHub | LeetCode`
- do not render duplicated labels like:
  - `LinkedIn: LinkedIn`
- project links should be inline on the project names in the body paragraph, for example:
  - `Fooddle`
  - `GlassFactory`
  - `Thapar University Timetable Portal`
- keep the phone number in the signature
- do not include a redundant plain email line in the signature

## When To Reuse A Template Pack

Reuse the template module when:

- the user wants the established outreach copy
- the module already matches the stage of the sequence
- only a few variables like `first_name` and `company` need to change

Write fresh inline copy when:

- the ask is materially different
- the existing pack’s tone or CTA is wrong
- the placeholders required by the user are not supported by the current template

Before falling back to inline copy, prefer creating a new template module under `mail_templates/` if the copy is likely to be reused even once more.

## When To Create A New Template Module

Create a new module or pack under `mail_templates/` when:

- no existing pack matches the outreach
- the user is describing a reusable outreach pattern
- the flow has multiple stages such as first email plus follow-ups

Suggested layout:

```text
mail_templates/
  campus_swe_outreach/
    __init__.py
    first_email.py
    follow_up_1.py
    follow_up_2.py
```

Each module should ideally expose:

- `render_subject(...)`
- `render_email(...)`
- `render_email_html(...)`
- `build_email(...)`

## How To Use In Runtime Scripts

Recommended pattern:

1. Load or derive row context
2. Import the template module
3. Call `build_email(...)`
4. Map the returned `subject` / `body` into scheduler jobs
   and include `body_html` when available so clickable links render properly

This can be done either:

- in a saved runtime script, or
- inline from bash using the environment's Python

For one-off tasks, prefer the inline route unless persistence helps.

Example:

```python
from mail_templates.generic_full_time_opportunity_mail.first_email import build_email as build_first
from mail_templates.generic_full_time_opportunity_mail.follow_up_1 import build_email as build_follow_up_1
from mail_templates.generic_full_time_opportunity_mail.follow_up_2 import build_email as build_follow_up_2

first = build_first(first_name=row["first_name"], company=row["company"])
second = build_follow_up_1(first_name=row["first_name"], company=row["company"])
third = build_follow_up_2(first_name=row["first_name"], company=row["company"])
```

If you create a new module first, the runtime script should import from that new path immediately instead of embedding the copy inline:

```python
from mail_templates.campus_swe_outreach.first_email import build_email as build_first

first = build_first(first_name=row["first_name"], company=row["company"])
job = {
    "recipient_email": row["recipient_email"],
    "subject": first["subject"],
    "body_text": first["body"],
    "body_html": first["body_html"],
}
```

HTML setup pattern:

```python
def build_email(*, first_name: str, company: str) -> dict[str, str]:
    return {
        "subject": render_subject(company=company),
        "body": render_email(first_name=first_name, company=company),
        "body_html": render_email_html(first_name=first_name, company=company),
    }
```

## Threaded Sequence Pattern

For a 3-email sequence:

- first email module -> root job
- follow_up_1 module -> child of root
- follow_up_2 module -> child of follow_up_1

The template pack provides the text only. Threading still comes from the API fields:

- `client_job_id`
- `parent_job_client_id`
- `reply_stop_enabled`

## Important Separation

Templates generate copy.

The runtime script is still responsible for:

- picking the right template module
- loading row data
- handling scheduling
- building allowed payloads
- posting to `/api/mail-jobs/bulk`
- verifying results

## Future Template Packs

If more packs are added later, follow the same convention when possible:

- predictable module names
- `build_email(...)` returning `subject`, `body`, and preferably `body_html`

That keeps runtime scripts short and easy for future agents to compose.

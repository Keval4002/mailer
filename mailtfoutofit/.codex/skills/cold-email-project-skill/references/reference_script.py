"""
REFERENCE SCRIPT — do not run as-is in production flows.

This file exists so the agent has one place to see every pattern needed
to drive this repo's mail scheduler: auth, resume preflight, tz-aware
scheduling, placeholder rendering, the persisted-field projection that
keeps POST /api/mail-jobs/bulk happy, bulk vs single send, and post-hoc
verification against /api/mail-jobs and /api/contacts.

At runtime the agent usually does two things:

1. create or update a reusable module under `mail_templates/`
2. write a NEW script tailored to one outreach that imports that template

Inline subject/body strings are the exception, not the default. Nothing
here is meant to be imported as a library.

See references/backend-api.md for the HTTP contract.
"""

from __future__ import annotations

import csv
import importlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests


# ─── 1. CONFIG ────────────────────────────────────────────────────────────────
# Prefer the public deployment first; MAIL_BASE_URL can override this
# to the local tunnel (`http://127.0.0.1:8011`) or any future host.
# Token comes from the same env var the backend itself reads (see
# mail_scheduler/config.py: API_BEARER_TOKEN).

BASE_URL = os.environ.get(
    "MAIL_BASE_URL",
    "http://mailtfoutofit.80.225.208.243.sslip.io",
)
API_BEARER_TOKEN = os.environ.get("API_BEARER_TOKEN")
APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY")
IST = timezone(timedelta(hours=5, minutes=30))

if not API_BEARER_TOKEN:
    # Agent-authored runtime scripts should surface this early, not
    # discover it after building payloads.
    raise RuntimeError("API_BEARER_TOKEN env var is required")

AUTH_HEADERS = {"Authorization": f"Bearer {API_BEARER_TOKEN}"}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}

# Apollo is enabled in this repo's `.env`, so agent-authored runtime
# scripts may read APOLLO_API_KEY and use it for pulling already-
# enriched contacts before projecting rows down to scheduler fields.


# ─── 2. TEMPLATE ──────────────────────────────────────────────────────────────
# The agent REWRITES both strings at runtime for the specific outreach.
# Only {placeholder} tokens here are variables; everything else is copy.
# Known-safe placeholders (populated by derive_name_fields / the data
# source): first_name, last_name, recipient_name, company, title,
# linkedin_url. Custom placeholders are fine — just make sure the data
# source provides them.

SUBJECT_TEMPLATE = "Quick note for {first_name} — re: {company}"

BODY_TEMPLATE = """\
Hi {first_name},

[Agent: rewrite this body at runtime with the actual pitch. Keep any
{placeholders} that correspond to keys present on every row of the
data source. Missing placeholders render as empty strings, not errors.]

Best,
Utkarsh
"""


class _SafeDict(dict):
    """dict that renders missing placeholders as '' instead of KeyError."""

    def __missing__(self, key: str) -> str:
        return ""


def render(template: str, ctx: dict) -> str:
    return template.format_map(_SafeDict(ctx))


def load_template_builder(module_path: str):
    """Load a repo-local mail template module that exposes build_email()."""
    module = importlib.import_module(module_path)
    builder = getattr(module, "build_email", None)
    if builder is None:
        raise AttributeError(f"{module_path} has no build_email()")
    return builder


def create_template_module_example() -> str:
    """Reference-only snippet for agents creating a new reusable template."""
    return '''from textwrap import dedent

def render_subject(*, company: str) -> str:
    return f"Quick note about opportunities at {company}"

def render_email(*, first_name: str, company: str) -> str:
    return dedent(
        f"""\\
        Hi {first_name},

        [Write reusable outreach copy here for {company}.]

        Best,
        Utkarsh
        """
    ).strip()

def build_email(*, first_name: str, company: str) -> dict[str, str]:
    return {
        "subject": render_subject(company=company),
        "body": render_email(first_name=first_name, company=company),
    }
'''


# ─── 3. DATA SOURCES ──────────────────────────────────────────────────────────
# Every source normalizes to list[dict]. The dicts can carry ANY extra
# columns (Apollo exports have ~40); the projection step in section 6
# strips them down to what the server accepts. This separation is why
# rich local context (first_name, city, seniority, etc.) stays usable
# for placeholder rendering even though it never hits the wire.

def from_manual() -> list[dict]:
    """One hand-written contact. Useful for testing and single sends."""
    return [
        {
            "recipient_email": "someone@example.com",
            "recipient_name": "Someone Example",
            "first_name": "Someone",
            "last_name": "Example",
            "company": "Amazon",
            "title": "Recruiter",
            "linkedin_url": "https://linkedin.com/in/example",
        }
    ]


def from_csv(path: str) -> list[dict]:
    """Generic CSV reader. Agent adds header-normalization logic here if
    the source (e.g. Apollo export) uses different column names."""
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def from_json(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and "contacts" in payload:
        return payload["contacts"]
    if isinstance(payload, list):
        return payload
    raise ValueError(f"Unsupported JSON shape in {path}")


def derive_name_fields(row: dict) -> dict:
    """Populate first_name / last_name / recipient_name from whatever
    is present. Local-only — these are for template rendering, not all
    of them get persisted by the server."""
    name = (row.get("recipient_name") or row.get("full_name") or "").strip()
    first = row.get("first_name") or (name.split(" ", 1)[0] if name else "")
    last = row.get("last_name") or (name.split(" ", 1)[1] if " " in name else "")
    row.setdefault("first_name", first)
    row.setdefault("last_name", last)
    if not row.get("recipient_name"):
        row["recipient_name"] = " ".join(part for part in (first, last) if part)
    return row


def normalize_common_fields(row: dict) -> dict:
    """A thin hook for runtime scripts to map varied inputs into the
    scheduler's expected keys."""
    if row.get("email") and not row.get("recipient_email"):
        row["recipient_email"] = row["email"]
    if row.get("name") and not row.get("recipient_name"):
        row["recipient_name"] = row["name"]
    return derive_name_fields(row)


# ─── 4. RESUME PREFLIGHT ──────────────────────────────────────────────────────
# Call this once, before building jobs, only when any job in the batch
# will carry attach_latest_resume=True. The server's scheduler picks
# whichever file row has kind='resume' AND is_active=true at send time;
# if there is none, the send will fail *later* in the loop, not at
# /bulk time — which is why an explicit preflight is worth 200ms.

def ensure_active_resume() -> dict:
    response = requests.get(f"{BASE_URL}/api/files", headers=AUTH_HEADERS, timeout=10)
    response.raise_for_status()
    files = response.json().get("files", [])
    active = next(
        (f for f in files if f.get("kind") == "resume" and f.get("is_active")),
        None,
    )
    if not active:
        raise RuntimeError(
            "No active resume file. Upload one via POST /api/files "
            "(multipart: file=<pdf>, kind=resume) before scheduling with "
            "attach_latest_resume=True."
        )
    return active


def post_workflow(batch_name: str, workflows: list[dict]) -> dict:
    """Submit a mixed email + LinkedIn workflow plan.

    Use this instead of /api/mail-jobs/bulk when the runtime agent wants
    explicit LinkedIn connect/message steps or cross-channel skip rules.
    """
    response = requests.post(
        f"{BASE_URL}/api/outreach-workflows/bulk",
        headers=JSON_HEADERS,
        json={"batch_name": batch_name, "workflows": workflows},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


# ─── 5. SCHEDULING ────────────────────────────────────────────────────────────
# scheduled_at MUST be timezone-aware; MailJobCreate in app.py rejects
# naive datetimes (validator raises, FastAPI returns 422). Default to
# IST because that's what the author runs on; override per outreach.

def schedule_sequence(
    rows: list[dict],
    start_at: datetime,
    gap_minutes: int = 3,
) -> list[tuple[dict, datetime]]:
    """Stagger sends so Gmail doesn't see a flood pattern. The scheduler
    loop in mail_scheduler/service.py picks jobs as their scheduled_at
    passes; small gaps keep deliverability healthy."""
    if start_at.tzinfo is None:
        raise ValueError("start_at must be timezone-aware")
    cursor = start_at
    out = []
    for row in rows:
        out.append((row, cursor))
        cursor += timedelta(minutes=gap_minutes)
    return out


def schedule_staggered_thread_starts(
    rows: list[dict],
    first_start_at: datetime,
    per_recipient_gap_minutes: int = 2,
) -> list[tuple[dict, datetime]]:
    """Assign each recipient a slightly different root send time.

    Why this exists:
    - If 100 recipients all start at the exact same minute, 100 first-touch
      emails will try to send together.
    - Their follow-ups will also bunch together at exactly T+2d / T+5d.
    - A small per-recipient offset produces a steadier delivery pattern and
      keeps each person's whole thread aligned to their own root send time.

    Example with per_recipient_gap_minutes=2:
    - recipient 1 -> 11:00
    - recipient 2 -> 11:02
    - recipient 3 -> 11:04

    Then each recipient's follow-ups stay anchored to that personal start time.
    """
    return schedule_sequence(
        rows,
        start_at=first_start_at,
        gap_minutes=per_recipient_gap_minutes,
    )


# ─── 6. PAYLOAD PROJECTION ────────────────────────────────────────────────────
# Pydantic model MailJobCreate in mail_scheduler/app.py uses
# ConfigDict(extra="forbid"). Any key outside this allowlist causes a
# 422 at /api/mail-jobs/bulk. Render templates with the FULL row (so
# {first_name} etc. work) but emit only these keys.

PERSISTED_FIELDS: frozenset[str] = frozenset(
    {
        "recipient_email",
        "recipient_name",
        "company",
        "title",
        "linkedin_url",
        "subject",
        "body_text",
        "body_html",
        "scheduled_at",
        "attach_latest_resume",
        # Thread-construction fields accepted by MailJobCreate. Without these
        # in the allowlist, one-call threaded bulk sends (agent.md §"threading")
        # silently degrade into unrelated root emails.
        "client_job_id",
        "parent_job_id",
        "parent_job_client_id",
        "root_job_id",
        "reply_stop_enabled",
    }
)


def build_job(
    row: dict,
    scheduled_at: datetime,
    *,
    attach_latest_resume: bool = True,
    subject_template: str = SUBJECT_TEMPLATE,
    body_template: str = BODY_TEMPLATE,
    body_html: str | None = None,
    client_job_id: str | None = None,
    parent_job_id: str | None = None,
    parent_job_client_id: str | None = None,
    root_job_id: str | None = None,
    reply_stop_enabled: bool | None = None,
) -> dict:
    if scheduled_at.tzinfo is None:
        raise ValueError("scheduled_at must be timezone-aware")
    ctx = dict(row)  # full context for placeholder rendering
    payload = {
        "recipient_email": (row.get("recipient_email") or "").strip().lower(),
        "recipient_name": row.get("recipient_name"),
        "company": row.get("company"),
        "title": row.get("title"),
        "linkedin_url": row.get("linkedin_url"),
        "subject": render(subject_template, ctx),
        "body_text": render(body_template, ctx),
        "body_html": render(body_html, ctx) if body_html else None,
        "scheduled_at": scheduled_at.isoformat(),
        "attach_latest_resume": attach_latest_resume,
        "client_job_id": client_job_id,
        "parent_job_id": parent_job_id,
        "parent_job_client_id": parent_job_client_id,
        "root_job_id": root_job_id,
        "reply_stop_enabled": reply_stop_enabled,
    }
    if not payload["recipient_email"]:
        raise ValueError(f"Row missing recipient_email: {row!r}")
    # Drop Nones so they don't override backend defaults, and defensively
    # intersect with the allowlist to guard against future edits above.
    return {
        key: value
        for key, value in payload.items()
        if key in PERSISTED_FIELDS and value is not None
    }


def build_thread_jobs(
    row: dict,
    start_at: datetime,
    *,
    first_email: dict,
    follow_up_1: dict | None = None,
    follow_up_2: dict | None = None,
    attach_latest_resume: bool = False,
    gap_minutes: int = 1,
) -> list[dict]:
    """Create a 1-3 email same-thread sequence in one bulk request."""
    if start_at.tzinfo is None:
        raise ValueError("start_at must be timezone-aware")

    jobs = [
        build_job(
            row,
            start_at,
            attach_latest_resume=attach_latest_resume,
            subject_template=first_email["subject"],
            body_template=first_email["body"],
            body_html=first_email.get("body_html"),
            client_job_id="root",
        )
    ]

    if follow_up_1 is not None:
        jobs.append(
            build_job(
                row,
                start_at + timedelta(minutes=gap_minutes),
                attach_latest_resume=False,
                subject_template=follow_up_1["subject"],
                body_template=follow_up_1["body"],
                body_html=follow_up_1.get("body_html"),
                client_job_id="follow-1",
                parent_job_client_id="root",
                reply_stop_enabled=True,
            )
        )

    if follow_up_2 is not None:
        parent_alias = "follow-1" if follow_up_1 is not None else "root"
        jobs.append(
            build_job(
                row,
                start_at + timedelta(minutes=gap_minutes * 2),
                attach_latest_resume=False,
                subject_template=follow_up_2["subject"],
                body_template=follow_up_2["body"],
                body_html=follow_up_2.get("body_html"),
                client_job_id="follow-2",
                parent_job_client_id=parent_alias,
                reply_stop_enabled=True,
            )
        )

    return jobs


# ─── 7. BULK SEND ─────────────────────────────────────────────────────────────
# The server accepts an arbitrary number of jobs per request, but large
# bodies are fragile over a tunnel and a 500 mid-request is awkward to
# recover from. Chunking gives per-chunk failure granularity.

@dataclass
class BulkResult:
    created_count: int
    job_ids: list[str]


def post_bulk(
    jobs: list[dict],
    batch_name: str | None = None,
    *,
    chunk_size: int = 50,
) -> BulkResult:
    if not jobs:
        return BulkResult(0, [])
    all_ids: list[str] = []
    for start in range(0, len(jobs), chunk_size):
        chunk = jobs[start : start + chunk_size]
        response = requests.post(
            f"{BASE_URL}/api/mail-jobs/bulk",
            headers=JSON_HEADERS,
            json={"batch_name": batch_name, "jobs": chunk},
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"/api/mail-jobs/bulk failed ({response.status_code}): {response.text}"
            )
        all_ids.extend(response.json().get("job_ids", []))
    return BulkResult(created_count=len(all_ids), job_ids=all_ids)


# ─── 8. SINGLE SEND ───────────────────────────────────────────────────────────
# There is no dedicated single-send endpoint on the server — the bulk
# endpoint handles a list of one just as well. Keeping this wrapper
# here so the agent doesn't invent a route that doesn't exist.

def post_single(job: dict, batch_name: str | None = None) -> BulkResult:
    return post_bulk([job], batch_name=batch_name)


# ─── 9. VERIFY ────────────────────────────────────────────────────────────────
# After /bulk returns, status is 'pending' — not 'sent'. These helpers
# are how the agent confirms what actually landed on the server.

def list_jobs_in_batch(batch_name: str) -> list[dict]:
    response = requests.get(
        f"{BASE_URL}/api/mail-jobs",
        headers=AUTH_HEADERS,
        params={"batch_name": batch_name},
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("mail_jobs", [])


def get_job(job_id: str) -> dict:
    response = requests.get(
        f"{BASE_URL}/api/mail-jobs/{job_id}", headers=AUTH_HEADERS, timeout=10
    )
    response.raise_for_status()
    return response.json()


def find_contact_by_email(email: str) -> dict | None:
    """GET /api/contacts uses LIKE matching (%email%); pass a full
    address to narrow to one row."""
    response = requests.get(
        f"{BASE_URL}/api/contacts",
        headers=AUTH_HEADERS,
        params={"email": email.lower()},
        timeout=15,
    )
    response.raise_for_status()
    contacts = response.json().get("contacts", [])
    for contact in contacts:
        if (contact.get("email") or "").lower() == email.lower():
            return contact
    return None


def get_contact_with_history(contact_id: str) -> dict:
    """Returns the contact plus a 'mail_jobs' array of every job for
    them — the cleanest way to audit what was scheduled for one
    recipient."""
    response = requests.get(
        f"{BASE_URL}/api/contacts/{contact_id}", headers=AUTH_HEADERS, timeout=10
    )
    response.raise_for_status()
    return response.json()


# ─── 10. MAIN ─────────────────────────────────────────────────────────────────
# Minimal end-to-end cookbook. DELETE and rewrite this at runtime.
# Default pattern: create or update a module under `mail_templates/`,
# then import it here. Inline-copy is only for throwaway one-offs.
# Order matters: preflight → load → derive → template/copy selection →
# schedule/build → post → verify.

def main() -> int:
    ensure_active_resume()

    raw_rows: list[dict] = from_manual()
    # raw_rows = from_csv("apollo-contacts.csv")
    # raw_rows = from_json("contacts.json")

    rows = [normalize_common_fields(dict(row)) for row in raw_rows]

    start_at = datetime.now(IST) + timedelta(hours=1)

    # Preferred path: import an existing or newly-created template module.
    first_builder = load_template_builder(
        "mail_templates.generic_full_time_opportunity_mail.first_email"
    )
    follow_1_builder = load_template_builder(
        "mail_templates.generic_full_time_opportunity_mail.follow_up_1"
    )
    follow_2_builder = load_template_builder(
        "mail_templates.generic_full_time_opportunity_mail.follow_up_2"
    )
    # For batches, stagger each recipient's root send time by a small amount
    # instead of scheduling every root email for the exact same minute.
    # This keeps delivery smoother and preserves the same relative timing for
    # each recipient's T / T+2d / T+5d thread.
    staggered = schedule_staggered_thread_starts(
        rows,
        first_start_at=start_at,
        per_recipient_gap_minutes=2,
    )
    jobs = []
    for row, row_start_at in staggered[:1]:
        jobs.extend(
            build_thread_jobs(
                row,
                start_at=row_start_at,
                first_email=first_builder(first_name=row["first_name"], company=row["company"]),
                follow_up_1=follow_1_builder(first_name=row["first_name"], company=row["company"]),
                follow_up_2=follow_2_builder(first_name=row["first_name"], company=row["company"]),
                attach_latest_resume=True,
                gap_minutes=1,
            )
        )

    # One-off inline-copy path, only when the user explicitly wants it:
    # scheduled = schedule_sequence(rows, start_at=start_at, gap_minutes=3)
    # jobs = [build_job(row, when, attach_latest_resume=True) for row, when in scheduled]

    batch_name = f"reference-{datetime.now(IST):%Y%m%d-%H%M%S}"
    result = post_bulk(jobs, batch_name=batch_name)
    print(f"Created {result.created_count} job(s) in batch {batch_name!r}")
    print(f"Job IDs: {result.job_ids}")

    for job in list_jobs_in_batch(batch_name):
        print(
            f"  • {job.get('recipient_email')} "
            f"→ {job.get('status')} @ {job.get('scheduled_at')}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings
from .gmail_api import build_authorization_url
from .service import MailSchedulerService

IST = ZoneInfo("Asia/Kolkata")


def _as_ist(value):
    if not value:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def format_human_datetime(value):
    parsed = _as_ist(value)
    if parsed is None:
        return "—"
    return parsed.strftime("%d %b %Y, %I:%M %p")


def format_datetime_input(value):
    parsed = _as_ist(value)
    if parsed is None:
        return ""
    return parsed.strftime("%Y-%m-%dT%H:%M")


def format_time_only(value):
    parsed = _as_ist(value)
    if parsed is None:
        return "—"
    return parsed.strftime("%H:%M")


def format_relative_time(value, now=None):
    parsed = _as_ist(value)
    if parsed is None:
        return ""
    reference = now or datetime.now(IST)
    delta = parsed - reference
    seconds = int(delta.total_seconds())
    suffix = "from now" if seconds >= 0 else "ago"
    seconds = abs(seconds)
    if seconds < 60:
        return "just now" if suffix == "ago" else "in <1m"
    minutes = seconds // 60
    if minutes < 60:
        return ("in %dm" % minutes) if suffix == "from now" else ("%dm ago" % minutes)
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if hours < 24:
        if suffix == "from now":
            return "in %dh %02dm" % (hours, remaining_minutes) if remaining_minutes else "in %dh" % hours
        return "%dh ago" % hours
    days = hours // 24
    if days < 7:
        return ("in %dd" % days) if suffix == "from now" else ("%dd ago" % days)
    return parsed.strftime("%d %b")


def normalize_ist_datetime_input(value: Optional[str]):
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    else:
        parsed = parsed.astimezone(IST)
    return parsed.isoformat()


def parse_optional_bool(value: Optional[str]):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise HTTPException(status_code=400, detail="Invalid boolean filter")


def pretty_json(value):
    if value in (None, "", {}):
        return "—"
    try:
        return json.dumps(value, indent=2, sort_keys=True)
    except TypeError:
        return str(value)





def _bucket_label(day: datetime, today: datetime):
    delta_days = (day.date() - today.date()).days
    if delta_days < 0:
        if delta_days == -1:
            return "Yesterday"
        if delta_days > -7:
            return day.strftime("%A")
        return day.strftime("%d %b %Y")
    if delta_days == 0:
        return "Today · %s" % day.strftime("%d %b")
    if delta_days == 1:
        return "Tomorrow · %s" % day.strftime("%d %b")
    if delta_days < 7:
        return day.strftime("%A · %d %b")
    return day.strftime("%d %b %Y")


def _build_thread_groups(threads: List[Dict], now: datetime):
    """Bucket threads by their most actionable moment.

    Precedence (highest first):
      1. "Needs attention" — any thread containing a failed mail.
      2. Time buckets (Today / Tomorrow / …) — keyed by next_actionable_at.
      3. "Waiting on reply" — all-sent threads with no reply.
      4. "Closed" — replied or fully terminal.
    """
    needs_attention = []
    waiting = []
    closed = []
    time_buckets: Dict[str, Dict] = {}
    time_order: List[str] = []

    for thread in threads:
        if thread.get("counts", {}).get("failed"):
            needs_attention.append(thread)
            continue
        state = thread.get("derived_state")
        if state == "active" and thread.get("next_actionable_at"):
            parsed = _as_ist(thread["next_actionable_at"])
            key = parsed.strftime("%Y-%m-%d") if parsed else "unscheduled"
            if key not in time_buckets:
                label = _bucket_label(parsed, now) if parsed else "Unscheduled"
                time_buckets[key] = {"key": key, "label": label, "threads": [], "tone": "default"}
                time_order.append(key)
            time_buckets[key]["threads"].append(thread)
        elif state == "waiting":
            waiting.append(thread)
        else:
            closed.append(thread)

    groups = []
    if needs_attention:
        groups.append({
            "key": "needs_attention",
            "label": "Needs attention",
            "tone": "danger",
            "threads": needs_attention,
        })
    for key in time_order:
        groups.append(time_buckets[key])
    if waiting:
        groups.append({"key": "waiting", "label": "Waiting on reply", "tone": "muted", "threads": waiting})
    if closed:
        groups.append({"key": "closed", "label": "Closed", "tone": "muted", "threads": closed})
    return groups


def _compute_pulse(jobs: List[Dict], active_resume: Optional[Dict], now: datetime):
    pending = [job for job in jobs if job.get("status") == "pending"]
    failed = [job for job in jobs if job.get("status") == "failed"]
    sent_sorted = sorted(
        (job for job in jobs if job.get("status") == "sent" and job.get("sent_at")),
        key=lambda j: j["sent_at"],
        reverse=True,
    )
    next_job = None
    for job in pending:
        parsed = _as_ist(job.get("requested_scheduled_at") or job.get("scheduled_at"))
        if parsed is None:
            continue
        if next_job is None:
            next_job = job
            continue
        current = _as_ist(next_job.get("requested_scheduled_at") or next_job.get("scheduled_at"))
        if parsed < current:
            next_job = job

    last_sent = sent_sorted[0] if sent_sorted else None
    return {
        "now": now,
        "pending_count": len(pending),
        "failed_count": len(failed),
        "next_job": next_job,
        "last_sent": last_sent,
        "active_resume": active_resume,
    }


class MailJobCreate(BaseModel):
    client_job_id: Optional[str] = None
    recipient_email: str
    recipient_name: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    linkedin_url: Optional[str] = None
    subject: str
    body_text: str
    body_html: Optional[str] = None
    scheduled_at: datetime
    attach_latest_resume: bool = True
    parent_job_id: Optional[str] = None
    parent_job_client_id: Optional[str] = None
    root_job_id: Optional[str] = None
    reply_stop_enabled: bool = True

    model_config = ConfigDict(extra="forbid")

    @field_validator("scheduled_at")
    @classmethod
    def validate_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError("scheduled_at must include a timezone offset")
        return value


class BulkMailJobCreate(BaseModel):
    batch_name: Optional[str] = None
    jobs: List[MailJobCreate]

    model_config = ConfigDict(extra="forbid")


class WorkflowContactCreate(BaseModel):
    recipient_email: Optional[str] = None
    recipient_name: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    linkedin_url: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class OutreachStepCreate(BaseModel):
    type: Literal["email", "linkedin"]
    kind: Optional[Literal["outreach", "connection_request", "message_after_acceptance"]] = None
    step_id: str
    scheduled_at: datetime
    subject: Optional[str] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    attach_latest_resume: bool = True
    thread_parent_step_id: Optional[str] = None
    reply_stop_enabled: bool = True
    message: Optional[str] = None
    requires_linkedin_connected: bool = False
    cancel_on_gmail_reply: bool = False
    cancel_on_linkedin_connected: bool = False

    model_config = ConfigDict(extra="forbid")

    @field_validator("scheduled_at")
    @classmethod
    def validate_workflow_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError("scheduled_at must include a timezone offset")
        return value


class WorkflowCreate(BaseModel):
    client_workflow_id: Optional[str] = None
    contact: WorkflowContactCreate
    steps: List[OutreachStepCreate]

    model_config = ConfigDict(extra="forbid")


class BulkWorkflowCreate(BaseModel):
    batch_name: Optional[str] = None
    workflows: List[WorkflowCreate]

    model_config = ConfigDict(extra="forbid")


class MailJobUpdate(BaseModel):
    subject: Optional[str] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    status: Optional[str] = Field(default=None, pattern="^(pending|failed|cancelled|blocked)$")

    model_config = ConfigDict(extra="forbid")

    @field_validator("scheduled_at")
    @classmethod
    def validate_timezone(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("scheduled_at must include a timezone offset")
        return value

class MailJobReschedule(BaseModel):
    new_time: str


def create_app(settings: Optional[Settings] = None):
    resolved_settings = settings or Settings.from_env()
    service = MailSchedulerService(resolved_settings)
    service.init_db()

    def require_api_token(authorization: Optional[str] = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token = authorization.split(" ", 1)[1]
        service.verify_api_token(token)

    async def scheduler_loop():
        next_inbox_sync = 0.0
        next_linkedin_sync = 0.0
        while True:
            current = asyncio.get_running_loop().time()
            if current >= next_inbox_sync:
                try:
                    service.sync_inbound_replies()
                except Exception:
                    pass
                try:
                    service.revive_failed_mail_jobs()
                except Exception:
                    pass
                next_inbox_sync = current + max(15, resolved_settings.gmail_poll_seconds)
            if current >= next_linkedin_sync:
                try:
                    service.revive_failed_linkedin_jobs()
                except Exception:
                    pass
                try:
                    service.sync_linkedin_jobs()
                except Exception:
                    pass
                next_linkedin_sync = current + max(15, resolved_settings.linkedin_status_sync_seconds)
            for job_id in service.get_due_job_ids():
                try:
                    service.send_mail_job(job_id)
                except Exception:
                    pass
            for linkedin_job_id in service.get_due_linkedin_job_ids():
                try:
                    service.dispatch_linkedin_job(linkedin_job_id)
                except Exception:
                    pass
            await asyncio.sleep(resolved_settings.scheduler_poll_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = resolved_settings
        app.state.service = service
        app.state.scheduler_task = None
        app.state.gmail_oauth_states = {}
        # On startup, any overdue pending jobs (e.g. from missing the 9am window)
        # will simply be caught by the scheduler_loop and sent immediately.
        # Hand off any pending jobs due in the next 24 h to Gmail Scheduled Send
        try:
            pushed = service.push_upcoming_jobs_to_gmail()
            if pushed:
                print(f"[Startup] {pushed} job(s) handed off to Gmail Scheduled Send.")
        except Exception as exc:
            print(f"[Startup] push_upcoming_jobs_to_gmail failed: {exc}")
        if resolved_settings.scheduler_enabled:
            app.state.scheduler_task = asyncio.create_task(scheduler_loop())
        try:
            yield
        finally:
            task = app.state.scheduler_task
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="Personal Mail Scheduler", lifespan=lifespan)
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Allows all origins for development and Cloudflare Pages
        allow_credentials=True,
        allow_methods=["*"],  # Allows all methods
        allow_headers=["*"],  # Allows all headers
    )

    app.state.settings = resolved_settings
    app.state.service = service
    app.state.scheduler_task = None
    app.state.gmail_oauth_states = {}

    def _active_resume():
        for item in service.list_files():
            if item.get("kind") == "resume" and item.get("is_active"):
                return item
        return None

    def _default_redirect_uri(request: Request, mode: Optional[str] = None):
        if mode == "local":
            return str(request.url_for("gmail_oauth_callback"))
        if resolved_settings.gmail_redirect_uri:
            return resolved_settings.gmail_redirect_uri
        if resolved_settings.public_base_url:
            return resolved_settings.public_base_url.rstrip("/") + "/auth/gmail/callback"
        return str(request.url_for("gmail_oauth_callback"))

    @app.post("/jobs/bulk")
    async def bulk_schedule_submit(request: Request):
        from datetime import timedelta
        import json
        import uuid

        form = await request.form()
        contacts_json_str = form.get("contacts_json", "[]")
        try:
            contacts = json.loads(contacts_json_str)
        except Exception:
            contacts = []

        start_date_raw = form.get("start_date")
        include_followups = bool(form.get("include_followups"))
        attach_resume = bool(form.get("attach_resume"))

        subject_0 = (form.get("subject_0") or "").strip()
        body_0 = (form.get("body_0") or "").strip()
        subject_1 = (form.get("subject_1") or "").strip()
        body_1 = (form.get("body_1") or "").strip()
        subject_2 = (form.get("subject_2") or "").strip()
        body_2 = (form.get("body_2") or "").strip()

        errors = []
        workflows = []

        if not start_date_raw:
            errors.append("Send Date (IST) is required.")
        if not body_0:
            errors.append("First Email body cannot be empty.")
        if not contacts:
            errors.append("No valid contacts found.")

        if not errors:
            start_date = datetime.fromisoformat(normalize_ist_datetime_input(start_date_raw))

            for i, c in enumerate(contacts):
                email = (c.get("email") or "").strip()
                if not email:
                    continue

                name = (c.get("name") or "").strip()
                company = (c.get("company") or "").strip()
                title = (c.get("title") or "").strip()

                first_name = name.split()[0] if name else "there"
                co_name = company if company else "your team"

                def format_text(text: str) -> str:
                    return text.replace("{name}", first_name).replace("{company}", co_name)

                steps = []
                step0_id = str(uuid.uuid4())
                steps.append({
                    "type": "email",
                    "kind": "outreach",
                    "step_id": step0_id,
                    "scheduled_at": start_date.isoformat(),
                    "subject": format_text(subject_0),
                    "body_text": format_text(body_0),
                    "body_html": format_text(body_0).replace("\n", "<br>"),
                    "attach_latest_resume": attach_resume,
                    "reply_stop_enabled": True,
                    "cancel_on_gmail_reply": True,
                })

                if include_followups:
                    step1_id = str(uuid.uuid4())
                    steps.append({
                        "type": "email",
                        "kind": "outreach",
                        "step_id": step1_id,
                        "scheduled_at": (start_date + timedelta(days=3)).isoformat(),
                        "subject": format_text(subject_1) if subject_1 else "",
                        "body_text": format_text(body_1),
                        "body_html": format_text(body_1).replace("\n", "<br>"),
                        "attach_latest_resume": False,
                        "thread_parent_step_id": step0_id if not subject_1 else None,
                        "reply_stop_enabled": True,
                        "cancel_on_gmail_reply": True,
                    })

                    steps.append({
                        "type": "email",
                        "kind": "outreach",
                        "step_id": str(uuid.uuid4()),
                        "scheduled_at": (start_date + timedelta(days=8)).isoformat(),
                        "subject": format_text(subject_2) if subject_2 else "",
                        "body_text": format_text(body_2),
                        "body_html": format_text(body_2).replace("\n", "<br>"),
                        "attach_latest_resume": False,
                        "thread_parent_step_id": step0_id if not subject_2 else None,
                        "reply_stop_enabled": True,
                        "cancel_on_gmail_reply": True,
                    })

                workflows.append({
                    "client_workflow_id": str(uuid.uuid4()),
                    "contact": {
                        "recipient_email": email,
                        "recipient_name": name or None,
                        "company": company or None,
                        "title": title or None,
                        "job_application_id": c.get("job_application_id"),
                    },
                    "steps": steps
                })

            if workflows:
                service.create_outreach_workflows("Campaign Builder", workflows)

        success = None
        if not errors and workflows:
            total_jobs = sum(len(w["steps"]) for w in workflows)
            success = {
                "contacts": len(workflows),
                "jobs": total_jobs,
                "followups": include_followups
            }

        if errors:
            return {"errors": errors}
        return {"success": success}

    @app.post("/jobs/new")
    async def job_create(
        recipient_email: str = Form(...),
        recipient_name: Optional[str] = Form(None),
        company: Optional[str] = Form(None),
        title: Optional[str] = Form(None),
        subject: str = Form(...),
        body_text: str = Form(...),
        scheduled_at: str = Form(...),
        attach_latest_resume: Optional[str] = Form(None),
    ):
        normalized_scheduled_at = normalize_ist_datetime_input(scheduled_at)
        job_data = {
            "recipient_email": recipient_email,
            "recipient_name": recipient_name,
            "company": company,
            "title": title,
            "subject": subject,
            "body_text": body_text,
            "scheduled_at": normalized_scheduled_at,
            "attach_latest_resume": bool(attach_latest_resume),
        }
        service.create_mail_jobs("Manual UI Job", [job_data])
        return RedirectResponse(url="/", status_code=303)

    @app.post("/jobs/{job_id}/edit")
    async def job_edit(
        job_id: str,
        subject: str = Form(...),
        body_text: str = Form(...),
        body_html: str = Form(""),
        scheduled_at: str = Form(...),
        status: str = Form(...),
    ):
        service.update_mail_job(
            job_id,
            {
                "subject": subject,
                "body_text": body_text,
                "body_html": body_html or None,
                "scheduled_at": normalize_ist_datetime_input(scheduled_at),
                "status": status,
            },
        )
        return RedirectResponse(url="/jobs/%s" % job_id, status_code=303)

    @app.post("/jobs/{job_id}/cancel")
    async def job_cancel(job_id: str):
        service.cancel_mail_job(job_id)
        return RedirectResponse(url="/jobs/%s" % job_id, status_code=303)

    @app.post("/jobs/{job_id}/send-now")
    async def job_send_now(job_id: str):
        service.send_mail_job(job_id)
        return RedirectResponse(url="/jobs/%s" % job_id, status_code=303)

    @app.get("/auth/gmail/start")
    async def gmail_oauth_start(request: Request, mode: Optional[str] = None):
        if not resolved_settings.gmail_client_id:
            raise HTTPException(status_code=500, detail="Gmail OAuth is not configured")
        redirect_uri = _default_redirect_uri(request, mode=mode)
        state = secrets.token_urlsafe(24)
        app.state.gmail_oauth_states[state] = redirect_uri
        url = build_authorization_url(
            resolved_settings.gmail_client_id,
            redirect_uri,
            state,
        )
        return RedirectResponse(url, status_code=302)

    @app.get("/auth/gmail/callback", name="gmail_oauth_callback")
    async def gmail_oauth_callback(request: Request, state: str, code: Optional[str] = None, error: Optional[str] = None):
        redirect_uri = app.state.gmail_oauth_states.pop(state, None)
        if redirect_uri is None:
            raise HTTPException(status_code=400, detail="Invalid OAuth state")
        if error:
            raise HTTPException(status_code=400, detail="Gmail OAuth failed: %s" % error)
        if not code:
            raise HTTPException(status_code=400, detail="Missing OAuth code")
        service.complete_gmail_oauth(code, redirect_uri)
        # Redirect to frontend after OAuth — PUBLIC_BASE_URL should point to the frontend
        frontend_url = resolved_settings.public_base_url or "http://localhost:3000"
        return RedirectResponse(url=frontend_url, status_code=303)

    @app.post("/auth/gmail/disconnect")
    async def gmail_oauth_disconnect():
        service.disconnect_gmail()
        # Return 200 JSON so the frontend can handle it without following the redirect
        return {"disconnected": True}

    @app.get("/auth/gmail/status")
    async def gmail_oauth_status():
        return service.get_gmail_auth_status()

    @app.post("/files/upload")
    async def files_upload(file: UploadFile = File(...), kind: str = Form(...)):
        service.store_file(file, kind)
        return RedirectResponse(url="/files", status_code=303)

    @app.post("/api/files", dependencies=[Depends(require_api_token)])
    async def upload_file(file: UploadFile = File(...), kind: str = Form(...)):
        return service.store_file(file, kind)

    @app.get("/api/files", dependencies=[Depends(require_api_token)])
    async def list_files():
        return {"files": service.list_files()}

    @app.get("/api/contacts", dependencies=[Depends(require_api_token)])
    async def list_contacts(
        company: Optional[str] = None,
        company_mode: str = "any",
        title: Optional[str] = None,
        email: Optional[str] = None,
        name: Optional[str] = None,
        country: Optional[str] = None,
        has_linkedin: Optional[bool] = None,
        has_sent_mail: Optional[bool] = None,
    ):
        return {
            "contacts": service.list_contacts(
                company=company,
                company_mode=company_mode,
                title=title,
                email=email,
                name=name,
                country=country,
                has_linkedin=has_linkedin,
                has_sent_mail=has_sent_mail,
            )
        }

    @app.get("/api/contacts/{contact_id}", dependencies=[Depends(require_api_token)])
    async def get_contact(contact_id: str):
        return service.get_contact(contact_id)

    @app.get("/api/contacts/{contact_id}/jobs", dependencies=[Depends(require_api_token)])
    async def get_contact_jobs(contact_id: str):
        with service.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, subject, body_text, scheduled_at, status, sent_at, failed_at,
                       parent_job_id, root_job_id, gmail_draft_id, gmail_scheduled_at,
                       last_error, blocked_reason
                FROM mail_jobs
                WHERE contact_id = ?
                ORDER BY scheduled_at ASC
                """,
                (contact_id,),
            ).fetchall()
            jobs = [dict(r) for r in rows]
        return {"jobs": jobs}

    @app.post("/api/contacts/{contact_id}/mark-replied", dependencies=[Depends(require_api_token)])
    async def mark_contact_replied(contact_id: str):
        result = service.mark_contact_replied(contact_id)
        return result

    @app.post("/api/contacts/{contact_id}/cancel-jobs", dependencies=[Depends(require_api_token)])
    async def cancel_contact_jobs(contact_id: str):
        result = service.cancel_contact_jobs(contact_id)
        return result

    @app.post("/api/mail-jobs/bulk", dependencies=[Depends(require_api_token)])
    async def create_bulk_mail_jobs(payload: BulkMailJobCreate):
        return service.create_mail_jobs(payload.batch_name, [job.model_dump() for job in payload.jobs])

    @app.post("/api/outreach-workflows/bulk", dependencies=[Depends(require_api_token)])
    async def create_bulk_outreach_workflows(payload: BulkWorkflowCreate):
        return service.create_outreach_workflows(
            payload.batch_name,
            [workflow.model_dump() for workflow in payload.workflows],
        )

    @app.get("/api/outreach-workflows", dependencies=[Depends(require_api_token)])
    async def list_outreach_workflows():
        return {"workflows": service.list_outreach_workflows()}

    @app.get("/api/outreach-workflows/{workflow_id}", dependencies=[Depends(require_api_token)])
    async def get_outreach_workflow(workflow_id: str):
        return service.get_outreach_workflow(workflow_id)

    @app.get("/api/linkedin-jobs", dependencies=[Depends(require_api_token)])
    async def list_linkedin_jobs(
        status: Optional[str] = None,
        email: Optional[str] = None,
        name: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        batch_name: Optional[str] = None,
    ):
        return {
            "linkedin_jobs": service.list_linkedin_jobs(
                status_filter=status,
                email=email,
                name=name,
                linkedin_url=linkedin_url,
                batch_name=batch_name,
            )
        }

    @app.get("/api/linkedin-jobs/{linkedin_job_id}", dependencies=[Depends(require_api_token)])
    async def get_linkedin_job(linkedin_job_id: str):
        return service.get_linkedin_job(linkedin_job_id)

    @app.get("/api/mail-jobs", dependencies=[Depends(require_api_token)])
    async def list_mail_jobs(
        status: Optional[str] = None,
        email: Optional[str] = None,
        name: Optional[str] = None,
        company: Optional[str] = None,
        title: Optional[str] = None,
        batch_name: Optional[str] = None,
        from_value: Optional[str] = None,
        to_value: Optional[str] = None,
    ):
        return {
            "mail_jobs": service.list_mail_jobs(
                status_filter=status,
                email=email,
                name=name,
                company=company,
                title=title,
                batch_name=batch_name,
                from_value=from_value,
                to_value=to_value,
            )
        }

    @app.get("/api/mail-jobs/{job_id}", dependencies=[Depends(require_api_token)])
    async def get_mail_job(job_id: str):
        return service.get_mail_job(job_id)

    @app.patch("/api/mail-jobs/{job_id}", dependencies=[Depends(require_api_token)])
    async def patch_mail_job(job_id: str, payload: MailJobUpdate):
        return service.update_mail_job(
            job_id,
            payload.model_dump(exclude_none=True),
        )

    @app.post("/api/mail-jobs/{job_id}/send-now", dependencies=[Depends(require_api_token)])
    async def api_send_now(job_id: str):
        return service.send_mail_job(job_id)

    @app.post("/api/mail-jobs/{job_id}/cancel", dependencies=[Depends(require_api_token)])
    async def api_cancel_job(job_id: str):
        return service.cancel_mail_job(job_id)

    @app.post("/api/mail-jobs/{job_id}/reschedule", dependencies=[Depends(require_api_token)])
    async def api_reschedule_job(job_id: str, payload: MailJobReschedule):
        return service.reschedule_job(job_id, payload.new_time)



    class ReminderCreate(BaseModel):
        title: str
        content_text: str

    @app.get("/api/reminders")
    async def get_reminders():
        return {"reminders": service.list_reminders()}

    @app.post("/api/reminders")
    async def create_reminder(payload: ReminderCreate):
        return service.create_reminder(payload.title, payload.content_text)

    @app.delete("/api/reminders/{reminder_id}")
    async def delete_reminder(reminder_id: str):
        service.delete_reminder(reminder_id)
        return {"success": True}

    class JobApplicationCreate(BaseModel):
        company_name: str
        role: Optional[str] = None
        job_url: Optional[str] = None
        job_board: Optional[str] = None
        status: Optional[str] = "Applied"
        notes: Optional[str] = None

    class JobApplicationUpdate(BaseModel):
        company_name: Optional[str] = None
        role: Optional[str] = None
        job_url: Optional[str] = None
        job_board: Optional[str] = None
        status: Optional[str] = None
        notes: Optional[str] = None

    @app.get("/api/applications/boards", dependencies=[Depends(require_api_token)])
    async def get_application_boards():
        return {"boards": service.list_job_applications_boards()}

    @app.get("/api/applications", dependencies=[Depends(require_api_token)])
    async def get_applications(
        search: Optional[str] = None,
        status: Optional[str] = None,
        job_board: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sort: str = "newest",
    ):
        return {
            "applications": service.list_job_applications(
                search=search,
                status=status,
                job_board=job_board,
                date_from=date_from,
                date_to=date_to,
                sort=sort,
            )
        }

    @app.post("/api/applications", dependencies=[Depends(require_api_token)])
    async def create_application(payload: JobApplicationCreate):
        return service.create_job_application(payload.model_dump(exclude_none=True))

    @app.patch("/api/applications/{app_id}", dependencies=[Depends(require_api_token)])
    async def update_application(app_id: str, payload: JobApplicationUpdate):
        return service.update_job_application(app_id, payload.model_dump(exclude_none=True))

    @app.delete("/api/applications/{app_id}", dependencies=[Depends(require_api_token)])
    async def delete_application(app_id: str):
        existing = service.get_job_application(app_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Application not found")
        service.delete_job_application(app_id)
        return {"ok": True}

    @app.post("/api/upload-image")
    async def upload_image(file: UploadFile = File(...)):
        import os
        import uuid
        upload_dir = Path(__file__).resolve().parent.parent.parent / "frontend-next" / "public" / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_ext = os.path.splitext(file.filename or "")[1]
        unique_name = f"{uuid.uuid4().hex}{file_ext}"
        file_path = upload_dir / unique_name
        with open(file_path, "wb") as buffer:
            import shutil
            shutil.copyfileobj(file.file, buffer)
        # Next.js serves files from public/ at the root path
        return {"url": f"/uploads/{unique_name}"}

    # Serve React Frontend
    frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")
        
        @app.get("/{full_path:path}")
        async def serve_frontend(full_path: str):
            if full_path.startswith("api/") or full_path.startswith("auth/"):
                raise HTTPException(status_code=404)
            # Check if a specific file exists in dist
            file_path = frontend_dist / full_path
            if file_path.is_file():
                return FileResponse(file_path)
            # Otherwise return index.html for React Router
            return FileResponse(frontend_dist / "index.html")
    
    return app


app = create_app()

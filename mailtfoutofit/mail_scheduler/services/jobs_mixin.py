import json
import os
import re
import secrets
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, UploadFile, status

from .. import gmail_api
from ..config import Settings


UTC = timezone.utc
MESSAGE_ID_PATTERN = re.compile(r"<[^>]+>")
OPENOUTREACH_CONNECTED_STATES = {"Connected", "Completed"}


class GmailAuthorizationError(RuntimeError):
    pass


def utc_now():
    return datetime.now(UTC)


def to_storage_datetime(value):
    return value.astimezone(UTC).isoformat()


def parse_datetime(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("scheduled_at must include a timezone offset")
    return parsed


def normalize_linkedin_url(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    parsed = urlparse(text)
    scheme = parsed.scheme or "https"
    netloc = (parsed.netloc or "").lower()
    path = re.sub(r"/+", "/", parsed.path or "").rstrip("/")
    if not netloc and path:
        return text.rstrip("/").lower()
    normalized = f"{scheme}://{netloc}{path}"
    return normalized.rstrip("/")


def _first_header_value(payload: Dict, header_name: str):
    headers = payload.get("payload", {}).get("headers", [])
    for header in headers:
        if header.get("name", "").lower() == header_name.lower():
            return header.get("value")
    return None


def _message_ids_from_header(value: Optional[str]):
    if not value:
        return []
    return MESSAGE_ID_PATTERN.findall(value)


def _max_history_id(left: Optional[str], right: Optional[str]):
    if left is None:
        return right
    if right is None:
        return left
    try:
        return str(max(int(left), int(right)))
    except ValueError:
        return max(left, right)


def _is_gmail_not_found_error(exc: Exception):
    if not isinstance(exc, gmail_api.GmailApiError):
        return False
    message = str(exc).lower()
    return "not_found" in message or "notfound" in message or '"code": 404' in message


def _is_retryable_openoutreach_failure(error_message: Optional[str]) -> bool:
    if not error_message:
        return False
    text = str(error_message).lower()
    indicators = (
        "connection refused",
        "name or service not known",
        "temporary failure in name resolution",
        "nodename nor servname provided",
        "failed to establish a new connection",
        "timed out",
        "timeout",
        "network is unreachable",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
    )
    return any(indicator in text for indicator in indicators)


def _is_retryable_gmail_failure(error_message: Optional[str]) -> bool:
    """Returns True only for transient failures that are safe to retry.

    Permanent failures (invalid address, hard bounce, policy rejection, etc.)
    must NOT be retried — they should stay 'failed' permanently.
    """
    if not error_message:
        return False
    text = str(error_message).lower()

    # Permanent / non-retryable patterns — return False immediately
    permanent_indicators = (
        "invalid_argument",
        "invalid argument",
        "invalid recipient",
        "no such user",
        "user unknown",
        "address rejected",
        "does not exist",
        "mailbox not found",
        "bad destination mailbox",
        "550",          # SMTP permanent failure
        "551",          # user not local
        "552",          # mailbox full / exceeded
        "553",          # mailbox name not allowed
        "554",          # transaction failed permanently
        "recipient address rejected",
        "invalid email",
    )
    if any(indicator in text for indicator in permanent_indicators):
        return False

    # Transient / retryable patterns
    retryable_indicators = (
        "gmail authorization expired",
        "invalid_grant",
        "token has been expired or revoked",
        "connect gmail before sending email",
        "rate limit",
        "quota exceeded",
        "backend error",
        "service unavailable",
        "temporarily",
        "try again",
    )
    return any(indicator in text for indicator in retryable_indicators)




class JobsMixin:
    def create_mail_jobs(self, batch_name: Optional[str], jobs: List[Dict]):
        created_ids = []
        now = to_storage_datetime(utc_now())
        with self.connect() as connection:
            prepared_jobs = []
            client_aliases = {}
            for job in jobs:
                parsed = parse_datetime(job["scheduled_at"])
                contact_id = self._upsert_contact(
                    connection,
                    job["recipient_email"],
                    job.get("recipient_name"),
                    job.get("company"),
                    job.get("title"),
                    job.get("linkedin_url"),
                    job_application_id=job.get("job_application_id"),
                )
                job_id = str(uuid.uuid4())
                client_job_id = self._clean_value(job.get("client_job_id"))
                if client_job_id:
                    if client_job_id in client_aliases:
                        raise HTTPException(status_code=400, detail="client_job_id values must be unique within the request")
                    client_aliases[client_job_id] = job_id
                prepared_jobs.append(
                    {
                        "job": job,
                        "job_id": job_id,
                        "contact_id": contact_id,
                        "parsed": parsed,
                        "client_job_id": client_job_id,
                    }
                )
            prepared_by_job_id = {item["job_id"]: item for item in prepared_jobs}
            request_root_cache = {}

            def resolve_request_parent(parent_job_id: str):
                if parent_job_id in request_root_cache:
                    return request_root_cache[parent_job_id]

                prepared_parent = prepared_by_job_id.get(parent_job_id)
                if prepared_parent is None:
                    return None

                parent_job = prepared_parent["job"]
                nested_parent_job_id = self._clean_value(parent_job.get("parent_job_id"))
                nested_parent_job_client_id = self._clean_value(parent_job.get("parent_job_client_id"))
                if nested_parent_job_client_id:
                    nested_parent_job_id = client_aliases.get(nested_parent_job_client_id)
                    if nested_parent_job_id is None:
                        raise HTTPException(status_code=400, detail="parent_job_client_id does not exist in this request")

                explicit_root_job_id = self._clean_value(parent_job.get("root_job_id"))
                if nested_parent_job_id:
                    nested_parent = resolve_request_parent(nested_parent_job_id)
                    if nested_parent is not None:
                        if nested_parent["contact_id"] != prepared_parent["contact_id"]:
                            raise HTTPException(status_code=400, detail="parent_job_id must belong to the same contact")
                        resolved = {
                            "contact_id": prepared_parent["contact_id"],
                            "root_job_id": explicit_root_job_id or nested_parent["root_job_id"],
                        }
                        request_root_cache[parent_job_id] = resolved
                        return resolved

                    parent_row = connection.execute(
                        "SELECT id, root_job_id, contact_id FROM mail_jobs WHERE id = ?",
                        (nested_parent_job_id,),
                    ).fetchone()
                    if parent_row is None:
                        raise HTTPException(status_code=400, detail="parent_job_id does not exist")
                    if parent_row["contact_id"] != prepared_parent["contact_id"]:
                        raise HTTPException(status_code=400, detail="parent_job_id must belong to the same contact")
                    resolved = {
                        "contact_id": prepared_parent["contact_id"],
                        "root_job_id": explicit_root_job_id or parent_row["root_job_id"] or parent_row["id"],
                    }
                    request_root_cache[parent_job_id] = resolved
                    return resolved

                resolved = {
                    "contact_id": prepared_parent["contact_id"],
                    "root_job_id": explicit_root_job_id or parent_job_id,
                }
                request_root_cache[parent_job_id] = resolved
                return resolved

            for prepared in prepared_jobs:
                job = prepared["job"]
                contact_id = prepared["contact_id"]
                parsed = prepared["parsed"]
                parent_job_id = self._clean_value(job.get("parent_job_id"))
                parent_job_client_id = self._clean_value(job.get("parent_job_client_id"))
                root_job_id = self._clean_value(job.get("root_job_id"))

                if parent_job_client_id:
                    resolved_parent_job_id = client_aliases.get(parent_job_client_id)
                    if resolved_parent_job_id is None:
                        raise HTTPException(status_code=400, detail="parent_job_client_id does not exist in this request")
                    parent_job_id = resolved_parent_job_id

                if parent_job_id:
                    request_parent = resolve_request_parent(parent_job_id)
                    if request_parent is not None:
                        if request_parent["contact_id"] != contact_id:
                            raise HTTPException(status_code=400, detail="parent_job_id must belong to the same contact")
                        root_job_id = root_job_id or request_parent["root_job_id"]
                    else:
                        parent_row = connection.execute(
                            "SELECT id, root_job_id, contact_id FROM mail_jobs WHERE id = ?",
                            (parent_job_id,),
                        ).fetchone()
                        if parent_row is None:
                            raise HTTPException(status_code=400, detail="parent_job_id does not exist")
                        if parent_row["contact_id"] != contact_id:
                            raise HTTPException(status_code=400, detail="parent_job_id must belong to the same contact")
                        root_job_id = root_job_id or parent_row["root_job_id"] or parent_row["id"]

                root_job_id = root_job_id or prepared["job_id"]
                connection.execute(
                    """
                    INSERT INTO mail_jobs (
                        id, contact_id, batch_name, source, subject, body_text, body_html,
                        scheduled_at, requested_scheduled_at, timezone_label, status,
                        attachment_mode, parent_job_id, root_job_id, reply_stop_enabled,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'local_ai', ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prepared["job_id"],
                        contact_id,
                        batch_name,
                        job["subject"],
                        job["body_text"],
                        job.get("body_html"),
                        to_storage_datetime(parsed),
                        parsed.isoformat(),
                        parsed.tzname() or parsed.isoformat()[-6:],
                        "latest_resume" if job.get("attach_latest_resume", True) else "none",
                        parent_job_id,
                        root_job_id,
                        1 if job.get("reply_stop_enabled", True) else 0,
                        now,
                        now,
                    ),
                )
                created_ids.append(prepared["job_id"])
        return {"created_count": len(created_ids), "job_ids": created_ids}

    def create_outreach_workflows(self, batch_name: Optional[str], workflows: List[Dict]):
        created_ids = []
        now = to_storage_datetime(utc_now())
        with self.connect() as connection:
            for workflow in workflows:
                contact_data = workflow.get("contact") or {}
                steps = workflow.get("steps") or []
                if not steps:
                    raise HTTPException(status_code=400, detail="workflow requires at least one step")
                contact_id = self._upsert_contact(
                    connection,
                    contact_data.get("recipient_email"),
                    contact_data.get("recipient_name"),
                    contact_data.get("company"),
                    contact_data.get("title"),
                    contact_data.get("linkedin_url"),
                    job_application_id=contact_data.get("job_application_id"),
                )
                # ── Duplicate-campaign guard ───────────────────────────────────────
                # If this contact already has an active workflow with pending/sent
                # jobs that haven't finished yet, skip creating another duplicate
                # sequence. This prevents the "3/9 jobs" issue where the same contact
                # gets submitted multiple times from the Campaign Builder.
                existing_active = connection.execute(
                    """
                    SELECT 1
                    FROM workflow_runs wr
                    JOIN mail_jobs mj ON mj.workflow_id = wr.id
                    WHERE wr.contact_id = ?
                      AND wr.status = 'active'
                      AND mj.status IN ('pending', 'gmail_scheduled')
                    LIMIT 1
                    """,
                    (contact_id,),
                ).fetchone()
                if existing_active:
                    # Contact already has a live sequence in progress — skip silently
                    continue
                # ──────────────────────────────────────────────────────────────────

                workflow_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO workflow_runs (
                        id, batch_name, client_workflow_id, contact_id, status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        workflow_id,
                        batch_name,
                        self._clean_value(workflow.get("client_workflow_id")),
                        contact_id,
                        now,
                        now,
                    ),
                )
                created_ids.append(workflow_id)


                mail_step_ids = {}
                mail_root_ids = {}
                linkedin_step_ids = {}
                for step in steps:
                    step_type = step.get("type")
                    step_id = self._clean_value(step.get("step_id"))
                    if not step_id:
                        raise HTTPException(status_code=400, detail="step_id is required")
                    if step_type == "email":
                        subject = self._clean_value(step.get("subject")) or ""
                        body_text = self._clean_value(step.get("body_text"))
                        parent_step_id = self._clean_value(step.get("thread_parent_step_id"))

                        if not body_text:
                            raise HTTPException(status_code=400, detail="email step requires body_text")
                        if not parent_step_id and not subject:
                            raise HTTPException(status_code=400, detail="initial email step requires subject")

                        parsed = parse_datetime(step["scheduled_at"])
                        job_id = str(uuid.uuid4())
                        parent_job_id = mail_step_ids.get(parent_step_id) if parent_step_id else None
                        if parent_step_id and not parent_job_id:
                            raise HTTPException(status_code=400, detail="thread_parent_step_id must reference an earlier email step")
                        root_job_id = mail_root_ids.get(parent_step_id, parent_job_id) if parent_job_id else job_id
                        connection.execute(
                            """
                            INSERT INTO mail_jobs (
                                id, contact_id, workflow_id, step_id, batch_name, source, subject, body_text, body_html,
                                scheduled_at, requested_scheduled_at, timezone_label, status, attachment_mode,
                                parent_job_id, root_job_id, reply_stop_enabled, cancel_on_linkedin_connected,
                                created_at, updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, 'workflow', ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                job_id,
                                contact_id,
                                workflow_id,
                                step_id,
                                batch_name,
                                subject,
                                body_text,
                                step.get("body_html"),
                                to_storage_datetime(parsed),
                                parsed.isoformat(),
                                parsed.tzname() or parsed.isoformat()[-6:],
                                "latest_resume" if step.get("attach_latest_resume", True) else "none",
                                parent_job_id,
                                root_job_id,
                                1 if step.get("reply_stop_enabled", True) else 0,
                                1 if step.get("cancel_on_linkedin_connected", False) else 0,
                                now,
                                now,
                            ),
                        )
                        mail_step_ids[step_id] = job_id
                        mail_root_ids[step_id] = root_job_id
                        continue

                    if step_type != "linkedin":
                        raise HTTPException(status_code=400, detail="unsupported step type")

                    linkedin_url = normalize_linkedin_url(contact_data.get("linkedin_url"))
                    if not linkedin_url:
                        raise HTTPException(status_code=400, detail="linkedin step requires contact.linkedin_url")
                    kind = self._clean_value(step.get("kind")) or "outreach"
                    if kind not in {"outreach", "connection_request", "message_after_acceptance"}:
                        raise HTTPException(status_code=400, detail="linkedin step requires a valid kind")
                    message = self._clean_value(step.get("message"))
                    if kind == "outreach" and not message:
                        raise HTTPException(status_code=400, detail="linkedin outreach step requires message")
                    if kind == "message_after_acceptance" and not message:
                        raise HTTPException(status_code=400, detail="message_after_acceptance step requires message")
                    parsed = parse_datetime(step["scheduled_at"])
                    linkedin_job_id = str(uuid.uuid4())
                    connection.execute(
                        """
                        INSERT INTO linkedin_jobs (
                            id, workflow_id, contact_id, step_id, batch_name, kind, linkedin_url, message,
                            scheduled_at, requested_scheduled_at, timezone_label, status, requires_linkedin_connected,
                            cancel_on_gmail_reply, cancel_on_linkedin_connected, openoutreach_campaign_id,
                            openoutreach_external_job_id, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            linkedin_job_id,
                            workflow_id,
                            contact_id,
                            step_id,
                            batch_name,
                            kind,
                            linkedin_url,
                            message,
                            to_storage_datetime(parsed),
                            parsed.isoformat(),
                            parsed.tzname() or parsed.isoformat()[-6:],
                            1 if step.get("requires_linkedin_connected", False) else 0,
                            1 if step.get("cancel_on_gmail_reply", False) else 0,
                            1 if step.get("cancel_on_linkedin_connected", False) else 0,
                            self._clean_value(self.settings.openoutreach_campaign_id),
                            linkedin_job_id,
                            now,
                            now,
                        ),
                    )
                    linkedin_step_ids[step_id] = linkedin_job_id

        return {"created_count": len(created_ids), "workflow_ids": created_ids}

    def _job_base_query(self):
        return """
            SELECT
                mail_jobs.*,
                contacts.email AS recipient_email,
                contacts.name AS recipient_name,
                contacts.company AS recipient_company,
                contacts.title AS recipient_title,
                contacts.linkedin_url AS recipient_linkedin_url,
                contacts.has_replied AS contact_has_replied,
                contacts.replied_at AS contact_replied_at
            FROM mail_jobs
            JOIN contacts ON contacts.id = mail_jobs.contact_id
        """

    def list_mail_jobs(
        self,
        status_filter: Optional[str] = None,
        email: Optional[str] = None,
        name: Optional[str] = None,
        company: Optional[str] = None,
        title: Optional[str] = None,
        batch_name: Optional[str] = None,
        from_value: Optional[str] = None,
        to_value: Optional[str] = None,
    ):
        clauses = []
        params = []
        if status_filter:
            clauses.append("mail_jobs.status = ?")
            params.append(status_filter)
        if email:
            clauses.append("contacts.email LIKE ?")
            params.append("%%%s%%" % email)
        if name:
            clauses.append("COALESCE(contacts.name, '') LIKE ?")
            params.append("%%%s%%" % name)
        if company:
            clauses.append("COALESCE(contacts.company, '') LIKE ?")
            params.append("%%%s%%" % company)
        if title:
            clauses.append("COALESCE(contacts.title, '') LIKE ?")
            params.append("%%%s%%" % title)
        if batch_name:
            clauses.append("mail_jobs.batch_name LIKE ?")
            params.append("%%%s%%" % batch_name)
        if from_value:
            parsed_from = parse_datetime(from_value)
            clauses.append("mail_jobs.scheduled_at >= ?")
            params.append(to_storage_datetime(parsed_from))
        if to_value:
            parsed_to = parse_datetime(to_value)
            clauses.append("mail_jobs.scheduled_at <= ?")
            params.append(to_storage_datetime(parsed_to))

        query = self._job_base_query()
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY mail_jobs.scheduled_at ASC, mail_jobs.created_at ASC"

        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["contact_has_replied"] = bool(item.get("contact_has_replied"))
        return items

    def list_outreach_workflows(self):
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT workflow_runs.*, contacts.email AS recipient_email, contacts.name AS recipient_name,
                       contacts.linkedin_url AS recipient_linkedin_url
                FROM workflow_runs
                JOIN contacts ON contacts.id = workflow_runs.contact_id
                ORDER BY workflow_runs.created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_outreach_workflow(self, workflow_id: str):
        with self.connect() as connection:
            workflow_row = connection.execute(
                """
                SELECT workflow_runs.*, contacts.email AS recipient_email, contacts.name AS recipient_name,
                       contacts.company AS recipient_company, contacts.title AS recipient_title,
                       contacts.linkedin_url AS recipient_linkedin_url
                FROM workflow_runs
                JOIN contacts ON contacts.id = workflow_runs.contact_id
                WHERE workflow_runs.id = ?
                """,
                (workflow_id,),
            ).fetchone()
            if workflow_row is None:
                raise HTTPException(status_code=404, detail="Workflow not found")
            mail_jobs = connection.execute(
                self._job_base_query() + " WHERE mail_jobs.workflow_id = ? ORDER BY mail_jobs.scheduled_at ASC, mail_jobs.created_at ASC",
                (workflow_id,),
            ).fetchall()
            linkedin_jobs = connection.execute(
                self._linkedin_job_base_query() + " WHERE linkedin_jobs.workflow_id = ? ORDER BY linkedin_jobs.scheduled_at ASC, linkedin_jobs.created_at ASC",
                (workflow_id,),
            ).fetchall()
            events = connection.execute(
                """
                SELECT * FROM workflow_events
                WHERE workflow_id = ?
                ORDER BY created_at ASC
                """,
                (workflow_id,),
            ).fetchall()
        item = dict(workflow_row)
        item["mail_jobs"] = [dict(row) for row in mail_jobs]
        item["linkedin_jobs"] = [dict(row) for row in linkedin_jobs]
        item["events"] = [dict(row) for row in events]
        return item

    def list_mail_job_threads(
        self,
        status_filter: Optional[str] = None,
        email: Optional[str] = None,
        name: Optional[str] = None,
        company: Optional[str] = None,
        title: Optional[str] = None,
        batch_name: Optional[str] = None,
        from_value: Optional[str] = None,
        to_value: Optional[str] = None,
        contact_id: Optional[str] = None,
    ) -> List[Dict]:
        """Group mail jobs into threads keyed by root_job_id.

        Filter semantics: a thread is included if any of its jobs matches the
        filters, but all of its jobs are returned (so the user sees the full
        thread in context). This is implemented by first finding matching roots,
        then loading every job that shares those roots.
        """
        matched = self.list_mail_jobs(
            status_filter=status_filter,
            email=email,
            name=name,
            company=company,
            title=title,
            batch_name=batch_name,
            from_value=from_value,
            to_value=to_value,
        )
        if contact_id:
            matched = [job for job in matched if job.get("contact_id") == contact_id]

        root_ids = {job.get("root_job_id") or job.get("id") for job in matched}
        if not root_ids:
            return []

        placeholders = ",".join("?" for _ in root_ids)
        query = (
            self._job_base_query()
            + " WHERE COALESCE(mail_jobs.root_job_id, mail_jobs.id) IN (%s)" % placeholders
            + " ORDER BY mail_jobs.scheduled_at ASC, mail_jobs.created_at ASC"
        )
        with self.connect() as connection:
            rows = connection.execute(query, list(root_ids)).fetchall()
            all_jobs = [dict(row) for row in rows]
            for job in all_jobs:
                job["contact_has_replied"] = bool(job.get("contact_has_replied"))

            thread_ids = {
                job.get("gmail_thread_id")
                for job in all_jobs
                if job.get("gmail_thread_id")
            }
            replies_by_thread: Dict[str, Dict] = {}
            if thread_ids:
                rep_placeholders = ",".join("?" for _ in thread_ids)
                reply_rows = connection.execute(
                    """
                    SELECT gmail_thread_id, from_email, subject, snippet, received_at
                    FROM inbound_messages
                    WHERE gmail_thread_id IN (%s)
                    ORDER BY received_at DESC, created_at DESC
                    """ % rep_placeholders,
                    list(thread_ids),
                ).fetchall()
                for row in reply_rows:
                    tid = row["gmail_thread_id"]
                    if tid and tid not in replies_by_thread:
                        replies_by_thread[tid] = dict(row)

        threads: Dict[str, Dict] = {}
        for job in all_jobs:
            rid = job.get("root_job_id") or job.get("id")
            bucket = threads.setdefault(rid, {"root_job_id": rid, "jobs": []})
            bucket["jobs"].append(job)

        result = []
        for rid, bucket in threads.items():
            jobs = bucket["jobs"]
            root = next((j for j in jobs if (j.get("id") == rid)), jobs[0])

            counts = {"sent": 0, "pending": 0, "failed": 0, "blocked": 0, "cancelled": 0}
            for job in jobs:
                status_key = job.get("status") or ""
                if status_key in counts:
                    counts[status_key] += 1

            actionable_candidates = [
                job.get("scheduled_at")
                for job in jobs
                if job.get("status") in ("failed", "pending")
                and job.get("scheduled_at")
            ]
            next_actionable_at = min(actionable_candidates) if actionable_candidates else None

            sent_times = [job.get("sent_at") for job in jobs if job.get("sent_at")]
            last_sent_at = max(sent_times) if sent_times else None

            reply = None
            for job in jobs:
                tid = job.get("gmail_thread_id")
                if tid and tid in replies_by_thread:
                    reply = replies_by_thread[tid]
                    break
            has_reply = reply is not None or any(j.get("contact_has_replied") for j in jobs)

            if counts["failed"] or counts["pending"]:
                derived_state = "active"
            elif counts["blocked"] and not has_reply:
                derived_state = "active"
            elif has_reply:
                derived_state = "closed"
            elif counts["sent"]:
                derived_state = "waiting"
            else:
                derived_state = "closed"

            result.append(
                {
                    "root_job_id": rid,
                    "contact_id": root.get("contact_id"),
                    "recipient_email": root.get("recipient_email"),
                    "recipient_name": root.get("recipient_name"),
                    "recipient_company": root.get("recipient_company"),
                    "recipient_title": root.get("recipient_title"),
                    "root_subject": root.get("subject"),
                    "batch_name": root.get("batch_name"),
                    "gmail_thread_id": root.get("gmail_thread_id"),
                    "jobs": jobs,
                    "counts": counts,
                    "total": len(jobs),
                    "has_reply": has_reply,
                    "reply": reply,
                    "next_actionable_at": next_actionable_at,
                    "last_sent_at": last_sent_at,
                    "derived_state": derived_state,
                }
            )

        def sort_key(thread: Dict):
            if thread["next_actionable_at"]:
                return (0, thread["next_actionable_at"])
            if thread["last_sent_at"]:
                return (1, thread["last_sent_at"])
            return (2, "")

        result.sort(key=sort_key)
        return result

    def get_mail_job(self, job_id: str):
        query = self._job_base_query() + " WHERE mail_jobs.id = ?"
        with self.connect() as connection:
            row = connection.execute(query, (job_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Mail job not found")
            item = dict(row)
            item["contact_has_replied"] = bool(item.get("contact_has_replied"))
            attempts = connection.execute(
                """
                SELECT id, mail_job_id, attempted_at, success, error_message, smtp_message_id
                FROM mail_send_attempts
                WHERE mail_job_id = ?
                ORDER BY attempted_at DESC
                """,
                (job_id,),
            ).fetchall()
            thread_jobs = connection.execute(
                self._job_base_query()
                + " WHERE mail_jobs.root_job_id = ? ORDER BY mail_jobs.scheduled_at ASC, mail_jobs.created_at ASC",
                (item["root_job_id"] or item["id"],),
            ).fetchall()
            inbound_messages = connection.execute(
                """
                SELECT *
                FROM inbound_messages
                WHERE matched_job_id = ? OR gmail_thread_id = ?
                ORDER BY received_at DESC, created_at DESC
                """,
                (job_id, item.get("gmail_thread_id")),
            ).fetchall()
        item["attempts"] = [dict(attempt) for attempt in attempts]
        item["thread_jobs"] = [dict(thread_job) for thread_job in thread_jobs]
        item["inbound_messages"] = [dict(message) for message in inbound_messages]
        return item

    def update_mail_job(self, job_id: str, payload: Dict):
        job = self.get_mail_job(job_id)
        if job["status"] == "sent":
            raise HTTPException(status_code=400, detail="Sent jobs cannot be edited")

        new_status = payload.get("status", job["status"])
        if new_status not in {"pending", "failed", "cancelled", "blocked"}:
            raise HTTPException(status_code=400, detail="Unsupported status update")

        scheduled_at = payload.get("scheduled_at", job["requested_scheduled_at"])
        parsed = parse_datetime(scheduled_at)
        updated_at = to_storage_datetime(utc_now())

        with self.connect() as connection:
            connection.execute(
                """
                UPDATE mail_jobs
                SET subject = ?, body_text = ?, body_html = ?, scheduled_at = ?,
                    requested_scheduled_at = ?, timezone_label = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.get("subject", job["subject"]),
                    payload.get("body_text", job["body_text"]),
                    payload.get("body_html", job["body_html"]),
                    to_storage_datetime(parsed),
                    parsed.isoformat(),
                    parsed.tzname() or parsed.isoformat()[-6:],
                    new_status,
                    updated_at,
                    job_id,
                ),
            )
        return self.get_mail_job(job_id)

    def cancel_mail_job(self, job_id: str):
        job = self.get_mail_job(job_id)
        if job["status"] != "pending":
            raise HTTPException(status_code=400, detail="Only pending jobs can be cancelled")
        with self.connect() as connection:
            connection.execute(
                "UPDATE mail_jobs SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (to_storage_datetime(utc_now()), job_id),
            )
        return self.get_mail_job(job_id)

    def get_due_job_ids(self):
        """Return IDs of pending jobs that are due AND whose parent (if any) has been sent.

        Follow-up jobs must wait until their parent is in 'sent' status before firing.
        If the parent is in a terminal non-sent state (failed/blocked/cancelled) the
        follow-up should already have been blocked; we skip it here defensively.
        """
        now = to_storage_datetime(utc_now())
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT mj.id
                FROM mail_jobs mj
                WHERE mj.status = 'pending'
                  AND mj.scheduled_at <= ?
                  AND (
                    -- No parent: initial email, always eligible
                    mj.parent_job_id IS NULL
                    OR
                    -- Parent exists and has been sent: follow-up is eligible
                    EXISTS (
                        SELECT 1 FROM mail_jobs p
                        WHERE p.id = mj.parent_job_id
                          AND p.status = 'sent'
                    )
                  )
                ORDER BY mj.scheduled_at ASC
                """,
                (now,),
            ).fetchall()
        return [row["id"] for row in rows]

    def reschedule_overdue_jobs(self) -> int:
        """On server startup, move ALL overdue pending jobs to the next 9 AM IST window.

        - If current IST time < 09:00 today  →  schedule for today at 09:00 IST
        - If current IST time >= 09:00 today →  schedule for tomorrow at 09:00 IST

        All overdue jobs get the SAME scheduled_at so they all send together in
        one batch (not staggered). Returns the number of jobs rescheduled.
        """
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")

        now_utc = utc_now()
        now_ist = now_utc.astimezone(IST)

        # Build today's 9 AM IST
        target_ist = now_ist.replace(hour=9, minute=0, second=0, microsecond=0)

        # If 9 AM has already passed, move to tomorrow
        if now_ist >= target_ist:
            target_ist = target_ist + timedelta(days=1)

        target_utc = target_ist.astimezone(timezone.utc)
        target_storage = to_storage_datetime(target_utc)
        now_storage = to_storage_datetime(now_utc)

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id FROM mail_jobs
                WHERE status = 'pending' AND scheduled_at < ?
                ORDER BY scheduled_at ASC
                """,
                (now_storage,),
            ).fetchall()

            if rows:
                connection.executemany(
                    "UPDATE mail_jobs SET scheduled_at = ?, updated_at = ? WHERE id = ?",
                    [(target_storage, now_storage, row["id"]) for row in rows],
                )

        count = len(rows)
        if count > 0:
            print(
                f"[Startup] Rescheduled {count} overdue job(s) → all set to "
                f"{target_ist.strftime('%d %b %Y at 9:00 AM IST')} (sent together)."
            )
        return count

    def revive_failed_mail_jobs(self):
        gmail_status = self.get_gmail_auth_status()
        if not gmail_status.get("connected") or gmail_status.get("last_error"):
            return {"revived_count": 0, "connected": bool(gmail_status.get("connected")), "ready": False}

        cutoff = to_storage_datetime(utc_now() - timedelta(hours=48))
        now = to_storage_datetime(utc_now())
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, last_error
                FROM mail_jobs
                WHERE status = 'failed'
                  AND created_at >= ?
                """,
                (cutoff,),
            ).fetchall()
            revived_ids = [
                row["id"]
                for row in rows
                if _is_retryable_gmail_failure(row["last_error"])
            ]
            if revived_ids:
                connection.executemany(
                    """
                    UPDATE mail_jobs
                    SET status = 'pending',
                        failed_at = NULL,
                        last_error = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    [(now, job_id) for job_id in revived_ids],
                )
        return {"revived_count": len(revived_ids), "connected": True, "ready": True}

    def _load_job_row(self, connection, job_id: str):
        row = connection.execute(self._job_base_query() + " WHERE mail_jobs.id = ?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Mail job not found")
        return dict(row)


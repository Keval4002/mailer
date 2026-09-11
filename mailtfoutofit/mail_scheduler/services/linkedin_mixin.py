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




class LinkedInMixin:
    def _linkedin_job_base_query(self):
        return """
            SELECT
                linkedin_jobs.*,
                contacts.email AS recipient_email,
                contacts.name AS recipient_name,
                contacts.company AS recipient_company,
                contacts.title AS recipient_title
            FROM linkedin_jobs
            JOIN contacts ON contacts.id = linkedin_jobs.contact_id
        """

    def list_linkedin_jobs(
        self,
        status_filter: Optional[str] = None,
        email: Optional[str] = None,
        name: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        batch_name: Optional[str] = None,
    ):
        clauses = []
        params = []
        if status_filter:
            clauses.append("linkedin_jobs.status = ?")
            params.append(status_filter)
        if email:
            clauses.append("COALESCE(contacts.email, '') LIKE ?")
            params.append("%%%s%%" % email.lower())
        if name:
            clauses.append("COALESCE(contacts.name, '') LIKE ?")
            params.append("%%%s%%" % name)
        if linkedin_url:
            clauses.append("COALESCE(linkedin_jobs.linkedin_url, '') LIKE ?")
            params.append("%%%s%%" % linkedin_url)
        if batch_name:
            clauses.append("COALESCE(linkedin_jobs.batch_name, '') LIKE ?")
            params.append("%%%s%%" % batch_name)

        query = self._linkedin_job_base_query()
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY COALESCE(linkedin_jobs.next_retry_at, linkedin_jobs.scheduled_at) ASC, linkedin_jobs.created_at ASC"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def get_linkedin_job(self, linkedin_job_id: str):
        with self.connect() as connection:
            row = connection.execute(
                self._linkedin_job_base_query() + " WHERE linkedin_jobs.id = ?",
                (linkedin_job_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="LinkedIn job not found")
            attempts = connection.execute(
                """
                SELECT id, linkedin_job_id, attempted_at, success, error_message, response_json
                FROM linkedin_job_attempts
                WHERE linkedin_job_id = ?
                ORDER BY attempted_at DESC
                """,
                (linkedin_job_id,),
            ).fetchall()
        item = dict(row)
        item["attempts"] = [dict(attempt) for attempt in attempts]
        return item

    def dispatch_linkedin_job(self, linkedin_job_id: str):
        job = self.get_linkedin_job(linkedin_job_id)
        if job["status"] not in {"pending", "failed"}:
            return job
        try:
            self._ensure_openoutreach_daemon()
            if job["kind"] == "outreach":
                response = self._dispatch_linkedin_outreach_job(job)
            elif job["kind"] == "connection_request":
                response = self._dispatch_linkedin_connection_job(job)
            else:
                response = self._dispatch_linkedin_message_job(job)
            success = True
            error_message = None
        except Exception as exc:
            response = None
            success = False
            error_message = str(exc)

        with self.connect() as connection:
            if success:
                self._record_linkedin_attempt(connection, linkedin_job_id, True, None, response)
            else:
                next_retry_at = to_storage_datetime(utc_now() + timedelta(seconds=self.settings.linkedin_retry_seconds))
                connection.execute(
                    """
                    UPDATE linkedin_jobs
                    SET status = 'failed',
                        attempt_count = attempt_count + 1,
                        next_retry_at = ?,
                        last_error = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        next_retry_at,
                        error_message,
                        to_storage_datetime(utc_now()),
                        linkedin_job_id,
                    ),
                )
                self._record_linkedin_attempt(connection, linkedin_job_id, False, error_message, None)
        return self.get_linkedin_job(linkedin_job_id)

    def get_due_linkedin_job_ids(self):
        now = to_storage_datetime(utc_now())
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM linkedin_jobs
                WHERE status IN ('pending', 'failed')
                  AND COALESCE(next_retry_at, scheduled_at) <= ?
                ORDER BY COALESCE(next_retry_at, scheduled_at) ASC, created_at ASC
                """,
                (now,),
            ).fetchall()
        return [row["id"] for row in rows]

    def _dispatch_linkedin_outreach_job(self, job: Dict):
        if not job.get("message"):
            raise RuntimeError("LinkedIn outreach job requires a message")
        response = self._openoutreach_request(
            "POST",
            "/api/manual/lead",
            json_body={
                "linkedin_url": job["linkedin_url"],
                "message": job["message"],
                "campaign_id": int(job["openoutreach_campaign_id"]) if job.get("openoutreach_campaign_id") else None,
            },
        )
        row = response.get("pipeline_row") or {}
        remote_state = row.get("state")
        new_status = "completed" if remote_state == "Completed" else "submitted"
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE linkedin_jobs
                SET status = ?, openoutreach_public_identifier = ?, openoutreach_deal_id = ?, next_retry_at = NULL,
                    last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_status,
                    response.get("public_identifier"),
                    str(response.get("deal_id") or "") or None,
                    to_storage_datetime(utc_now()),
                    job["id"],
                ),
            )
            if remote_state in OPENOUTREACH_CONNECTED_STATES:
                if self._record_workflow_event(
                    connection,
                    job["workflow_id"],
                    job["contact_id"],
                    "linkedin_connected",
                    {"state": remote_state, "public_identifier": response.get("public_identifier")},
                ):
                    self._block_mail_jobs_for_linkedin_connected(connection, job["workflow_id"])
            if remote_state == "Completed":
                self._record_workflow_event(
                    connection,
                    job["workflow_id"],
                    job["contact_id"],
                    "linkedin_message_completed",
                    {"public_identifier": response.get("public_identifier")},
                )
        return response

    def _dispatch_linkedin_connection_job(self, job: Dict):
        sibling_message = None
        with self.connect() as connection:
            sibling_message = connection.execute(
                self._linkedin_job_base_query() + """
                WHERE linkedin_jobs.workflow_id = ?
                  AND linkedin_jobs.kind = 'message_after_acceptance'
                ORDER BY linkedin_jobs.created_at ASC
                LIMIT 1
                """,
                (job["workflow_id"],),
            ).fetchone()
        body = {
            "linkedin_url": job["linkedin_url"],
            "campaign_id": int(job["openoutreach_campaign_id"]) if job.get("openoutreach_campaign_id") else None,
        }
        if sibling_message is not None and sibling_message["message"]:
            body["message"] = sibling_message["message"]
            body["external_job_id"] = sibling_message["id"]
        response = self._openoutreach_request("POST", "/api/manual/connect", json_body=body)
        row = response.get("pipeline_row") or {}
        with self.connect() as connection:
            new_status = "completed" if row.get("state") in OPENOUTREACH_CONNECTED_STATES else "submitted"
            connection.execute(
                """
                UPDATE linkedin_jobs
                SET status = ?, openoutreach_public_identifier = ?, openoutreach_deal_id = ?, next_retry_at = NULL,
                    last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_status,
                    response.get("public_identifier"),
                    str(response.get("deal_id") or "") or None,
                    to_storage_datetime(utc_now()),
                    job["id"],
                ),
            )
            if row.get("state") in OPENOUTREACH_CONNECTED_STATES:
                if self._record_workflow_event(
                    connection,
                    job["workflow_id"],
                    job["contact_id"],
                    "linkedin_connected",
                    {"state": row.get("state"), "public_identifier": response.get("public_identifier")},
                ):
                    self._block_mail_jobs_for_linkedin_connected(connection, job["workflow_id"])
        return response

    def _dispatch_linkedin_message_job(self, job: Dict):
        self._sync_one_linkedin_job(job)
        refreshed = self.get_linkedin_job(job["id"])
        if refreshed["status"] == "completed":
            return {"ok": True, "already_completed": True}
        with self.connect() as connection:
            if refreshed.get("requires_linkedin_connected") and not self._workflow_event_exists(
                connection,
                refreshed["workflow_id"],
                "linkedin_connected",
            ):
                connection.execute(
                    """
                    UPDATE linkedin_jobs
                    SET status = 'waiting_connection', updated_at = ?
                    WHERE id = ?
                    """,
                    (to_storage_datetime(utc_now()), refreshed["id"]),
                )
                return {"ok": True, "waiting_connection": True}
        response = self._openoutreach_request(
            "POST",
            "/api/manual/message/activate",
            json_body={
                "linkedin_url": refreshed["linkedin_url"],
                "campaign_id": int(refreshed["openoutreach_campaign_id"]) if refreshed.get("openoutreach_campaign_id") else None,
                "external_job_id": refreshed["id"],
            },
        )
        row = response.get("pipeline_row") or {}
        with self.connect() as connection:
            new_status = "completed" if row.get("state") == "Completed" else "submitted"
            connection.execute(
                """
                UPDATE linkedin_jobs
                SET status = ?, openoutreach_public_identifier = ?, openoutreach_deal_id = ?, next_retry_at = NULL,
                    last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_status,
                    response.get("public_identifier"),
                    str(response.get("deal_id") or "") or None,
                    to_storage_datetime(utc_now()),
                    refreshed["id"],
                ),
            )
            if new_status == "completed":
                self._record_workflow_event(
                    connection,
                    refreshed["workflow_id"],
                    refreshed["contact_id"],
                    "linkedin_message_completed",
                    {"public_identifier": response.get("public_identifier")},
                )
        return response

    def revive_failed_linkedin_jobs(self):
        try:
            self._ensure_openoutreach_daemon()
            self._openoutreach_request("GET", "/api/manual/daemon/status")
        except Exception:
            return {"revived_count": 0, "reachable": False}

        cutoff = to_storage_datetime(utc_now() - timedelta(hours=48))
        now = to_storage_datetime(utc_now())
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, last_error
                FROM linkedin_jobs
                WHERE status = 'failed'
                  AND created_at >= ?
                """,
                (cutoff,),
            ).fetchall()
            revived_ids = [
                row["id"]
                for row in rows
                if _is_retryable_openoutreach_failure(row["last_error"])
            ]
            if revived_ids:
                connection.executemany(
                    """
                    UPDATE linkedin_jobs
                    SET status = 'pending',
                        next_retry_at = NULL,
                        last_error = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    [(now, job_id) for job_id in revived_ids],
                )
        return {"revived_count": len(revived_ids), "reachable": True}

    def sync_linkedin_jobs(self):
        with self.connect() as connection:
            rows = connection.execute(
                self._linkedin_job_base_query() + """
                WHERE linkedin_jobs.status IN ('pending', 'submitted', 'waiting_connection', 'failed')
                  AND linkedin_jobs.openoutreach_public_identifier IS NOT NULL
                ORDER BY linkedin_jobs.updated_at ASC
                """
            ).fetchall()
        synced = 0
        for row in rows:
            self._sync_one_linkedin_job(dict(row))
            synced += 1
        return {"synced_count": synced}

    def _sync_one_linkedin_job(self, linkedin_job: Dict):
        with self.connect() as connection:
            current = connection.execute(
                self._linkedin_job_base_query() + " WHERE linkedin_jobs.id = ?",
                (linkedin_job["id"],),
            ).fetchone()
        if current is None:
            return None
        linkedin_job = dict(current)
        campaign_id = linkedin_job.get("openoutreach_campaign_id") or self._clean_value(self.settings.openoutreach_campaign_id)
        if not campaign_id or not linkedin_job.get("openoutreach_public_identifier"):
            return None
        payload = self._openoutreach_request(
            "GET",
            f"/api/manual/pipeline?campaign_id={campaign_id}&public_identifier={linkedin_job['openoutreach_public_identifier']}",
        )
        rows = payload.get("leads") or []
        if not rows:
            return None
        row = rows[0]
        now = to_storage_datetime(utc_now())
        with self.connect() as connection:
            local = dict(current)
            new_status = local["status"]
            remote_state = row.get("state")
            if local["kind"] == "outreach":
                if remote_state == "Completed":
                    new_status = "completed"
                elif remote_state in {"Qualified", "Pending"} | OPENOUTREACH_CONNECTED_STATES:
                    new_status = "submitted"
            elif local["kind"] == "connection_request":
                if remote_state in OPENOUTREACH_CONNECTED_STATES:
                    new_status = "completed"
                elif remote_state == "Pending":
                    new_status = "submitted"
            else:
                if remote_state == "Completed":
                    new_status = "completed"
                elif remote_state in OPENOUTREACH_CONNECTED_STATES and local["status"] == "waiting_connection":
                    new_status = "pending"

            connection.execute(
                """
                UPDATE linkedin_jobs
                SET status = ?, openoutreach_deal_id = ?, openoutreach_public_identifier = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_status,
                    str(row.get("deal_id") or "") or None,
                    row.get("public_identifier"),
                    now,
                    local["id"],
                ),
            )
            if remote_state in OPENOUTREACH_CONNECTED_STATES:
                if self._record_workflow_event(
                    connection,
                    local["workflow_id"],
                    local["contact_id"],
                    "linkedin_connected",
                    {"state": remote_state, "public_identifier": row.get("public_identifier")},
                ):
                    self._block_mail_jobs_for_linkedin_connected(connection, local["workflow_id"])
            if local["kind"] == "message_after_acceptance" and remote_state == "Completed":
                self._record_workflow_event(
                    connection,
                    local["workflow_id"],
                    local["contact_id"],
                    "linkedin_message_completed",
                    {"public_identifier": row.get("public_identifier")},
                )
        return row

    def _record_linkedin_attempt(self, connection, linkedin_job_id: str, success: bool, error_message: Optional[str], response_json: Optional[Dict]):
        connection.execute(
            """
            INSERT INTO linkedin_job_attempts (id, linkedin_job_id, attempted_at, success, error_message, response_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                linkedin_job_id,
                to_storage_datetime(utc_now()),
                1 if success else 0,
                error_message,
                json.dumps(response_json) if response_json is not None else None,
            ),
        )

    def _block_mail_jobs_for_linkedin_connected(self, connection, workflow_id: str):
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'blocked',
                blocked_reason = 'linkedin_connected',
                updated_at = ?
            WHERE workflow_id = ?
              AND cancel_on_linkedin_connected = 1
              AND status = 'pending'
            """,
            (to_storage_datetime(utc_now()), workflow_id),
        )


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




class MiscMixin:
    def _workflow_event_exists(self, connection, workflow_id: str, event_type: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM workflow_events WHERE workflow_id = ? AND event_type = ? LIMIT 1",
            (workflow_id, event_type),
        ).fetchone()
        return row is not None

    def _record_workflow_event(self, connection, workflow_id: str, contact_id: str, event_type: str, payload: Optional[Dict] = None) -> bool:
        if self._workflow_event_exists(connection, workflow_id, event_type):
            return False
        connection.execute(
            """
            INSERT INTO workflow_events (id, workflow_id, contact_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                workflow_id,
                contact_id,
                event_type,
                json.dumps(payload) if payload is not None else None,
                to_storage_datetime(utc_now()),
            ),
        )
        return True

    def list_reminders(self):
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM reminders ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def create_reminder(self, title: str, content_text: str):
        import uuid
        from datetime import datetime
        now = to_storage_datetime(datetime.now(UTC))
        reminder_id = str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO reminders (id, title, content_text, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (reminder_id, title, content_text, now, now)
            )
            cursor = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
            return dict(cursor.fetchone())

    def delete_reminder(self, reminder_id: str):
        with self.connect() as conn:
            conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))

    def list_job_applications(
        self,
        search: Optional[str] = None,
        status: Optional[str] = None,
        job_board: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sort: str = "newest",
    ):
        conditions = []
        params = []

        if search:
            like = f"%{search}%"
            conditions.append("(company_name LIKE ? OR role LIKE ?)")
            params.extend([like, like])

        if status:
            # Support comma-separated multi-status filter
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            if statuses:
                placeholders = ",".join("?" * len(statuses))
                conditions.append(f"status IN ({placeholders})")
                params.extend(statuses)

        if job_board:
            conditions.append("job_board = ?")
            params.append(job_board)

        if date_from:
            conditions.append("applied_at >= ?")
            params.append(date_from)

        if date_to:
            conditions.append("applied_at <= ?")
            params.append(date_to)

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        order_map = {
            "newest": "applied_at DESC, created_at DESC",
            "oldest": "applied_at ASC, created_at ASC",
            "company": "company_name ASC",
        }
        order_clause = f"ORDER BY {order_map.get(sort, 'applied_at DESC, created_at DESC')}"

        sql = f"SELECT * FROM job_applications {where_clause} {order_clause}"
        with self.connect() as conn:
            cursor = conn.execute(sql, tuple(params))
            return [dict(row) for row in cursor.fetchall()]

    def list_job_applications_boards(self):
        """Return distinct non-null job boards for filter dropdowns."""
        with self.connect() as conn:
            cursor = conn.execute(
                "SELECT DISTINCT job_board FROM job_applications WHERE job_board IS NOT NULL AND job_board != '' ORDER BY job_board"
            )
            return [row[0] for row in cursor.fetchall()]

    def get_job_application(self, application_id: str):
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM job_applications WHERE id = ?", (application_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def create_job_application(self, data: dict):
        import uuid
        app_id = str(uuid.uuid4())
        now = to_storage_datetime(utc_now())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO job_applications (
                    id, company_name, role, job_url, job_board, status, notes, applied_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    app_id,
                    data.get("company_name"),
                    data.get("role"),
                    data.get("job_url"),
                    data.get("job_board"),
                    data.get("status", "Applied"),
                    data.get("notes"),
                    data.get("applied_at", now),
                    now,
                    now,
                )
            )
            return self.get_job_application(app_id)

    def update_job_application(self, application_id: str, updates: dict):
        if not updates:
            return self.get_job_application(application_id)
        
        now = to_storage_datetime(utc_now())
        set_clauses = []
        values = []
        for key, value in updates.items():
            if key in ["company_name", "role", "job_url", "job_board", "status", "notes"]:
                set_clauses.append(f"{key} = ?")
                values.append(value)
        
        if not set_clauses:
            return self.get_job_application(application_id)
            
        set_clauses.append("updated_at = ?")
        values.append(now)
        values.append(application_id)
        
        with self.connect() as conn:
            conn.execute(
                f"UPDATE job_applications SET {', '.join(set_clauses)} WHERE id = ?",
                tuple(values)
            )
            return self.get_job_application(application_id)

    def delete_job_application(self, application_id: str):
        with self.connect() as conn:
            conn.execute("DELETE FROM job_applications WHERE id = ?", (application_id,))

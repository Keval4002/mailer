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

from . import gmail_api
from .config import Settings


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
    if not error_message:
        return False
    text = str(error_message).lower()
    indicators = (
        "gmail authorization expired",
        "invalid_grant",
        "token has been expired or revoked",
        "connect gmail before sending email",
    )
    return any(indicator in text for indicator in indicators)


class MailSchedulerService:
    def __init__(self, settings: Settings):
        self.settings = settings
        database_dir = os.path.dirname(self.settings.database_path)
        if database_dir:
            os.makedirs(database_dir, exist_ok=True)
        os.makedirs(self.settings.files_dir, exist_ok=True)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.settings.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @contextmanager
    def connect_openoutreach(self):
        if not self.settings.openoutreach_database_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OpenOutreach database connection is not configured",
            )
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="psycopg is required for the OpenOutreach dashboard",
            ) from exc

        connection = psycopg.connect(
            self.settings.openoutreach_database_url,
            row_factory=dict_row,
        )
        try:
            connection.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            yield connection
        finally:
            connection.close()

    def init_db(self):
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    mime_type TEXT,
                    created_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS contacts (
                    id TEXT PRIMARY KEY,
                    apollo_person_id TEXT UNIQUE,
                    apollo_organization_id TEXT,
                    email TEXT UNIQUE,
                    linkedin_key TEXT UNIQUE,
                    email_status TEXT,
                    name TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    linkedin_url TEXT,
                    title TEXT,
                    headline TEXT,
                    company TEXT,
                    company_domain TEXT,
                    city TEXT,
                    state TEXT,
                    country TEXT,
                    formatted_address TEXT,
                    timezone_name TEXT,
                    seniority TEXT,
                    departments_json TEXT,
                    employment_history_json TEXT,
                    apollo_raw_json TEXT,
                    source TEXT NOT NULL DEFAULT 'apollo',
                    has_replied INTEGER NOT NULL DEFAULT 0,
                    replied_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_synced_at TEXT
                );

                CREATE TABLE IF NOT EXISTS mail_jobs (
                    id TEXT PRIMARY KEY,
                    contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                    workflow_id TEXT REFERENCES workflow_runs(id) ON DELETE SET NULL,
                    step_id TEXT,
                    batch_name TEXT,
                    source TEXT NOT NULL DEFAULT 'local_ai',
                    subject TEXT NOT NULL,
                    body_text TEXT NOT NULL,
                    body_html TEXT,
                    scheduled_at TEXT NOT NULL,
                    requested_scheduled_at TEXT NOT NULL,
                    timezone_label TEXT,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'cancelled', 'blocked')),
                    attachment_mode TEXT NOT NULL CHECK (attachment_mode IN ('none', 'latest_resume')),
                    message_id TEXT,
                    gmail_message_id TEXT,
                    gmail_thread_id TEXT,
                    parent_job_id TEXT,
                    root_job_id TEXT,
                    reply_stop_enabled INTEGER NOT NULL DEFAULT 1,
                    cancel_on_linkedin_connected INTEGER NOT NULL DEFAULT 0,
                    blocked_reason TEXT,
                    sent_at TEXT,
                    failed_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    content_text TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workflow_runs (
                    id TEXT PRIMARY KEY,
                    batch_name TEXT,
                    client_workflow_id TEXT,
                    contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'blocked', 'cancelled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS linkedin_jobs (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
                    contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                    step_id TEXT NOT NULL,
                    batch_name TEXT,
                    kind TEXT NOT NULL CHECK (kind IN ('outreach', 'connection_request', 'message_after_acceptance')),
                    linkedin_url TEXT NOT NULL,
                    message TEXT,
                    scheduled_at TEXT NOT NULL,
                    requested_scheduled_at TEXT NOT NULL,
                    timezone_label TEXT,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'waiting_connection', 'submitted', 'completed', 'failed', 'blocked', 'cancelled')),
                    requires_linkedin_connected INTEGER NOT NULL DEFAULT 0,
                    cancel_on_gmail_reply INTEGER NOT NULL DEFAULT 0,
                    cancel_on_linkedin_connected INTEGER NOT NULL DEFAULT 0,
                    openoutreach_campaign_id TEXT,
                    openoutreach_public_identifier TEXT,
                    openoutreach_deal_id TEXT,
                    openoutreach_external_job_id TEXT,
                    next_retry_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS linkedin_job_attempts (
                    id TEXT PRIMARY KEY,
                    linkedin_job_id TEXT NOT NULL REFERENCES linkedin_jobs(id) ON DELETE CASCADE,
                    attempted_at TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    error_message TEXT,
                    response_json TEXT
                );

                CREATE TABLE IF NOT EXISTS workflow_events (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
                    contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mail_send_attempts (
                    id TEXT PRIMARY KEY,
                    mail_job_id TEXT NOT NULL REFERENCES mail_jobs(id) ON DELETE CASCADE,
                    attempted_at TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    error_message TEXT,
                    smtp_message_id TEXT
                );

                CREATE TABLE IF NOT EXISTS gmail_auth_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    email TEXT,
                    access_token TEXT,
                    refresh_token TEXT,
                    expiry TEXT,
                    scope TEXT,
                    history_id TEXT,
                    last_inbox_sync_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS inbound_messages (
                    id TEXT PRIMARY KEY,
                    contact_id TEXT REFERENCES contacts(id) ON DELETE SET NULL,
                    matched_job_id TEXT REFERENCES mail_jobs(id) ON DELETE SET NULL,
                    gmail_message_id TEXT,
                    gmail_thread_id TEXT,
                    message_id TEXT,
                    in_reply_to TEXT,
                    references_raw TEXT,
                    from_email TEXT,
                    subject TEXT,
                    snippet TEXT,
                    received_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_mail_jobs_status_scheduled_at
                ON mail_jobs(status, scheduled_at);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_inbound_messages_gmail_message_id
                ON inbound_messages(gmail_message_id)
                WHERE gmail_message_id IS NOT NULL;
                """
            )
            self._migrate_contacts_table(connection)
            self._migrate_mail_jobs_table(connection)
            self._migrate_linkedin_jobs_table(connection)
            self._migrate_mail_send_attempts_table(connection)
            self._migrate_inbound_messages_table(connection)
            self._migrate_gmail_auth_table(connection)
            self._create_contact_indexes(connection)
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_mail_jobs_root_contact_status
                ON mail_jobs(root_job_id, contact_id, status);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_jobs_message_id
                ON mail_jobs(message_id)
                WHERE message_id IS NOT NULL;

                CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_runs_contact_client
                ON workflow_runs(contact_id, client_workflow_id)
                WHERE client_workflow_id IS NOT NULL;

                CREATE UNIQUE INDEX IF NOT EXISTS idx_linkedin_jobs_workflow_step
                ON linkedin_jobs(workflow_id, step_id);

                CREATE INDEX IF NOT EXISTS idx_linkedin_jobs_status_scheduled_at
                ON linkedin_jobs(status, scheduled_at);

                CREATE INDEX IF NOT EXISTS idx_workflow_events_workflow_created
                ON workflow_events(workflow_id, created_at);
                """
            )

    def openoutreach_dashboard_enabled(self):
        return bool(self.settings.openoutreach_database_url)

    def _openoutreach_deal_base_query(self):
        return """
            SELECT
                d.id AS deal_id,
                l.id AS lead_id,
                c.id AS campaign_id,
                c.name AS campaign_name,
                c.manual_mode,
                l.public_identifier,
                l.linkedin_url,
                l.urn,
                l.disqualified,
                l.creation_date AS lead_created_at,
                l.update_date AS lead_updated_at,
                CASE WHEN l.embedding IS NOT NULL THEN TRUE ELSE FALSE END AS has_embedding,
                COALESCE(octet_length(l.embedding), 0) AS embedding_bytes,
                d.state,
                d.outcome,
                d.reason,
                d.connect_attempts,
                d.backoff_hours,
                d.creation_date AS deal_created_at,
                d.update_date AS deal_updated_at,
                CASE WHEN d.profile_summary IS NOT NULL THEN TRUE ELSE FALSE END AS has_profile_summary,
                CASE WHEN d.chat_summary IS NOT NULL THEN TRUE ELSE FALSE END AS has_chat_summary,
                CASE WHEN mom.id IS NOT NULL THEN TRUE ELSE FALSE END AS has_message,
                COALESCE(mom.enabled, FALSE) AS message_enabled,
                mom.external_job_id,
                mom.update_date AS message_updated_at,
                next_task.task_type AS next_task_type,
                next_task.status AS next_task_status,
                next_task.scheduled_at AS next_task_scheduled_at
            FROM crm_deal d
            JOIN crm_lead l ON l.id = d.lead_id
            JOIN linkedin_campaign c ON c.id = d.campaign_id
            LEFT JOIN linkedin_manualoutreachmessage mom ON mom.deal_id = d.id
            LEFT JOIN LATERAL (
                SELECT
                    t.task_type,
                    t.status,
                    t.scheduled_at
                FROM linkedin_task t
                WHERE
                    t.status = 'pending'
                    AND COALESCE(t.payload->>'public_id', '') = l.public_identifier
                    AND COALESCE((t.payload->>'campaign_id')::int, -1) = c.id
                ORDER BY t.scheduled_at ASC
                LIMIT 1
            ) AS next_task ON TRUE
        """

    def list_openoutreach_deals(
        self,
        query: Optional[str] = None,
        state: Optional[str] = None,
        campaign_id: Optional[int] = None,
        manual_mode: Optional[bool] = None,
        message_state: Optional[str] = None,
        has_embedding: Optional[bool] = None,
        has_summary: Optional[bool] = None,
        disqualified: Optional[bool] = None,
        has_pending_task: Optional[bool] = None,
        sort: str = "updated_desc",
    ):
        clauses = []
        params: List[object] = []
        if query:
            pattern = "%%%s%%" % query
            clauses.append(
                """
                (
                    l.public_identifier ILIKE %s OR
                    COALESCE(l.linkedin_url, '') ILIKE %s OR
                    COALESCE(c.name, '') ILIKE %s OR
                    COALESCE(d.reason, '') ILIKE %s OR
                    COALESCE(mom.message, '') ILIKE %s OR
                    COALESCE(d.profile_summary::text, '') ILIKE %s OR
                    COALESCE(d.chat_summary::text, '') ILIKE %s
                )
                """
            )
            params.extend([pattern] * 7)
        if state:
            clauses.append("d.state = %s")
            params.append(state)
        if campaign_id is not None:
            clauses.append("c.id = %s")
            params.append(int(campaign_id))
        if manual_mode is not None:
            clauses.append("c.manual_mode = %s")
            params.append(bool(manual_mode))
        if message_state == "enabled":
            clauses.append("mom.id IS NOT NULL AND mom.enabled = TRUE")
        elif message_state == "disabled":
            clauses.append("mom.id IS NOT NULL AND mom.enabled = FALSE")
        elif message_state == "missing":
            clauses.append("mom.id IS NULL")
        if has_embedding is not None:
            clauses.append("l.embedding IS NOT NULL" if has_embedding else "l.embedding IS NULL")
        if has_summary is not None:
            summary_clause = "(d.profile_summary IS NOT NULL OR d.chat_summary IS NOT NULL)"
            clauses.append(summary_clause if has_summary else f"NOT {summary_clause}")
        if disqualified is not None:
            clauses.append("l.disqualified = %s")
            params.append(bool(disqualified))
        if has_pending_task is not None:
            clauses.append("next_task.task_type IS NOT NULL" if has_pending_task else "next_task.task_type IS NULL")

        order_by = {
            "updated_desc": "d.update_date DESC, d.id DESC",
            "created_desc": "d.creation_date DESC, d.id DESC",
            "state": "d.state ASC, d.update_date DESC, d.id DESC",
            "next_task": "next_task.scheduled_at ASC NULLS LAST, d.update_date DESC, d.id DESC",
        }.get(sort, "d.update_date DESC, d.id DESC")

        query_sql = self._openoutreach_deal_base_query()
        if clauses:
            query_sql += " WHERE " + " AND ".join(clauses)
        query_sql += " ORDER BY " + order_by

        with self.connect_openoutreach() as connection:
            rows = connection.execute(query_sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_openoutreach_dashboard(
        self,
        query: Optional[str] = None,
        state: Optional[str] = None,
        campaign_id: Optional[int] = None,
        manual_mode: Optional[bool] = None,
        message_state: Optional[str] = None,
        has_embedding: Optional[bool] = None,
        has_summary: Optional[bool] = None,
        disqualified: Optional[bool] = None,
        has_pending_task: Optional[bool] = None,
        sort: str = "updated_desc",
    ):
        deals = self.list_openoutreach_deals(
            query=query,
            state=state,
            campaign_id=campaign_id,
            manual_mode=manual_mode,
            message_state=message_state,
            has_embedding=has_embedding,
            has_summary=has_summary,
            disqualified=disqualified,
            has_pending_task=has_pending_task,
            sort=sort,
        )
        with self.connect_openoutreach() as connection:
            queue_rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM linkedin_task
                GROUP BY status
                """
            ).fetchall()
            campaign_rows = connection.execute(
                """
                SELECT id, name
                FROM linkedin_campaign
                ORDER BY lower(name) ASC, id ASC
                """
            ).fetchall()

        state_counts: Dict[str, int] = {}
        for item in deals:
            state_name = item.get("state") or "Unknown"
            state_counts[state_name] = state_counts.get(state_name, 0) + 1

        queue_counts = {row["status"]: row["count"] for row in queue_rows}
        summary = {
            "total_deals": len(deals),
            "qualified_count": state_counts.get("Qualified", 0),
            "pending_count": state_counts.get("Pending", 0),
            "connected_count": state_counts.get("Connected", 0),
            "completed_count": state_counts.get("Completed", 0),
            "failed_count": state_counts.get("Failed", 0),
            "disqualified_count": sum(1 for item in deals if item.get("disqualified")),
            "message_enabled_count": sum(1 for item in deals if item.get("message_enabled")),
            "has_embedding_count": sum(1 for item in deals if item.get("has_embedding")),
            "has_summary_count": sum(
                1 for item in deals if item.get("has_profile_summary") or item.get("has_chat_summary")
            ),
            "task_pending_count": queue_counts.get("pending", 0),
            "task_running_count": queue_counts.get("running", 0),
            "task_failed_count": queue_counts.get("failed", 0),
            "task_completed_count": queue_counts.get("completed", 0),
            "state_counts": state_counts,
        }
        return {
            "summary": summary,
            "deals": deals,
            "campaigns": [dict(row) for row in campaign_rows],
        }

    def get_openoutreach_deal(self, deal_id: int):
        with self.connect_openoutreach() as connection:
            row = connection.execute(
                """
                SELECT
                    d.id AS deal_id,
                    l.id AS lead_id,
                    c.id AS campaign_id,
                    c.name AS campaign_name,
                    c.manual_mode,
                    l.public_identifier,
                    l.linkedin_url,
                    l.urn,
                    l.disqualified,
                    l.creation_date AS lead_created_at,
                    l.update_date AS lead_updated_at,
                    CASE WHEN l.embedding IS NOT NULL THEN TRUE ELSE FALSE END AS has_embedding,
                    COALESCE(octet_length(l.embedding), 0) AS embedding_bytes,
                    d.state,
                    d.outcome,
                    d.reason,
                    d.connect_attempts,
                    d.backoff_hours,
                    d.creation_date AS deal_created_at,
                    d.update_date AS deal_updated_at,
                    d.profile_summary,
                    d.chat_summary,
                    CASE WHEN mom.id IS NOT NULL THEN TRUE ELSE FALSE END AS has_message,
                    COALESCE(mom.enabled, FALSE) AS message_enabled,
                    mom.message,
                    mom.external_job_id,
                    mom.creation_date AS message_created_at,
                    mom.update_date AS message_updated_at
                FROM crm_deal d
                JOIN crm_lead l ON l.id = d.lead_id
                JOIN linkedin_campaign c ON c.id = d.campaign_id
                LEFT JOIN linkedin_manualoutreachmessage mom ON mom.deal_id = d.id
                WHERE d.id = %s
                """,
                (deal_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="OpenOutreach deal not found")

            tasks = connection.execute(
                """
                SELECT
                    id,
                    task_type,
                    status,
                    scheduled_at,
                    payload,
                    created_at,
                    started_at,
                    completed_at
                FROM linkedin_task
                WHERE
                    COALESCE(payload->>'public_id', '') = %s
                    AND COALESCE((payload->>'campaign_id')::int, -1) = %s
                ORDER BY scheduled_at DESC, created_at DESC
                """,
                (row["public_identifier"], row["campaign_id"]),
            ).fetchall()

            recent_connect_tasks = connection.execute(
                """
                SELECT
                    id,
                    task_type,
                    status,
                    scheduled_at,
                    payload,
                    created_at,
                    started_at,
                    completed_at
                FROM linkedin_task
                WHERE
                    task_type = 'connect'
                    AND COALESCE((payload->>'campaign_id')::int, -1) = %s
                ORDER BY scheduled_at DESC, created_at DESC
                LIMIT 5
                """,
                (row["campaign_id"],),
            ).fetchall()

        item = dict(row)
        item["tasks"] = [dict(task) for task in tasks]
        item["recent_connect_tasks"] = [dict(task) for task in recent_connect_tasks]
        return item

    def _table_columns(self, connection, table_name: str):
        rows = connection.execute("PRAGMA table_info(%s)" % table_name).fetchall()
        return {row["name"] for row in rows}

    def _table_sql(self, connection, table_name: str):
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row["sql"] if row else ""

    def _migrate_contacts_table(self, connection):
        existing_columns = self._table_columns(connection, "contacts")
        current_sql = self._table_sql(connection, "contacts")
        email_not_nullable = bool(re.search(r"\bemail\s+TEXT\b[^,]*\bNOT\s+NULL\b", current_sql, re.IGNORECASE))
        if email_not_nullable:
            legacy_columns = set(existing_columns)
            def legacy_expr(column_name: str, fallback: str = "NULL"):
                return column_name if column_name in legacy_columns else fallback

            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("ALTER TABLE contacts RENAME TO contacts_old")
            connection.execute(
                """
                CREATE TABLE contacts (
                    id TEXT PRIMARY KEY,
                    apollo_person_id TEXT UNIQUE,
                    apollo_organization_id TEXT,
                    email TEXT UNIQUE,
                    linkedin_key TEXT UNIQUE,
                    email_status TEXT,
                    name TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    linkedin_url TEXT,
                    title TEXT,
                    headline TEXT,
                    company TEXT,
                    company_domain TEXT,
                    city TEXT,
                    state TEXT,
                    country TEXT,
                    formatted_address TEXT,
                    timezone_name TEXT,
                    seniority TEXT,
                    departments_json TEXT,
                    employment_history_json TEXT,
                    apollo_raw_json TEXT,
                    source TEXT NOT NULL DEFAULT 'apollo',
                    has_replied INTEGER NOT NULL DEFAULT 0,
                    replied_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_synced_at TEXT
                )
                """
            )
            connection.execute(
                f"""
                INSERT INTO contacts (
                    id, apollo_person_id, apollo_organization_id, email, linkedin_key, email_status, name,
                    first_name, last_name, linkedin_url, title, headline, company, company_domain, city,
                    state, country, formatted_address, timezone_name, seniority, departments_json,
                    employment_history_json, apollo_raw_json, source, has_replied, replied_at,
                    created_at, updated_at, last_synced_at
                )
                SELECT
                    id,
                    {legacy_expr('apollo_person_id')},
                    {legacy_expr('apollo_organization_id')},
                    {legacy_expr('email')},
                    COALESCE({legacy_expr('linkedin_key')}, {legacy_expr('linkedin_url')}),
                    {legacy_expr('email_status')},
                    {legacy_expr('name')},
                    {legacy_expr('first_name')},
                    {legacy_expr('last_name')},
                    {legacy_expr('linkedin_url')},
                    {legacy_expr('title')},
                    {legacy_expr('headline')},
                    {legacy_expr('company')},
                    {legacy_expr('company_domain')},
                    {legacy_expr('city')},
                    {legacy_expr('state')},
                    {legacy_expr('country')},
                    {legacy_expr('formatted_address')},
                    {legacy_expr('timezone_name')},
                    {legacy_expr('seniority')},
                    {legacy_expr('departments_json')},
                    {legacy_expr('employment_history_json')},
                    {legacy_expr('apollo_raw_json')},
                    COALESCE({legacy_expr('source')}, 'apollo'),
                    COALESCE({legacy_expr('has_replied', '0')}, 0),
                    {legacy_expr('replied_at')},
                    created_at,
                    updated_at,
                    {legacy_expr('last_synced_at', 'updated_at')}
                FROM contacts_old
                """
            )
            connection.execute("DROP TABLE contacts_old")
            connection.execute("PRAGMA foreign_keys = ON")
            existing_columns = self._table_columns(connection, "contacts")

        desired_columns = {
            "apollo_person_id": "TEXT",
            "apollo_organization_id": "TEXT",
            "linkedin_key": "TEXT",
            "email_status": "TEXT",
            "first_name": "TEXT",
            "last_name": "TEXT",
            "headline": "TEXT",
            "company_domain": "TEXT",
            "city": "TEXT",
            "state": "TEXT",
            "country": "TEXT",
            "formatted_address": "TEXT",
            "timezone_name": "TEXT",
            "seniority": "TEXT",
            "departments_json": "TEXT",
            "employment_history_json": "TEXT",
            "apollo_raw_json": "TEXT",
            "source": "TEXT NOT NULL DEFAULT 'apollo'",
            "has_replied": "INTEGER NOT NULL DEFAULT 0",
            "replied_at": "TEXT",
            "last_synced_at": "TEXT",
        }
        for column_name, column_type in desired_columns.items():
            if column_name not in existing_columns:
                connection.execute("ALTER TABLE contacts ADD COLUMN %s %s" % (column_name, column_type))

        connection.execute(
            """
            UPDATE contacts
            SET source = CASE
                    WHEN source IS NULL OR source = '' THEN 'local_ai'
                    WHEN source = 'apollo' AND (apollo_person_id IS NULL OR TRIM(apollo_person_id) = '') THEN 'local_ai'
                    ELSE source
                END,
                linkedin_key = COALESCE(linkedin_key, linkedin_url),
                has_replied = COALESCE(has_replied, 0),
                updated_at = COALESCE(updated_at, created_at),
                last_synced_at = COALESCE(last_synced_at, updated_at)
            """
        )

    def _migrate_linkedin_jobs_table(self, connection):
        existing_columns = self._table_columns(connection, "linkedin_jobs")
        required_columns = {
            "workflow_id",
            "contact_id",
            "step_id",
            "batch_name",
            "kind",
            "linkedin_url",
            "message",
            "scheduled_at",
            "requested_scheduled_at",
            "timezone_label",
            "status",
            "requires_linkedin_connected",
            "cancel_on_gmail_reply",
            "cancel_on_linkedin_connected",
            "openoutreach_campaign_id",
            "openoutreach_public_identifier",
            "openoutreach_deal_id",
            "openoutreach_external_job_id",
            "next_retry_at",
            "attempt_count",
            "last_error",
            "created_at",
            "updated_at",
        }
        current_sql = self._table_sql(connection, "linkedin_jobs")
        needs_rebuild = not required_columns.issubset(existing_columns) or "'outreach'" not in current_sql
        if not needs_rebuild:
            return

        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("ALTER TABLE linkedin_jobs RENAME TO linkedin_jobs_old")
        connection.execute(
            """
            CREATE TABLE linkedin_jobs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
                contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                step_id TEXT NOT NULL,
                batch_name TEXT,
                kind TEXT NOT NULL CHECK (kind IN ('outreach', 'connection_request', 'message_after_acceptance')),
                linkedin_url TEXT NOT NULL,
                message TEXT,
                scheduled_at TEXT NOT NULL,
                requested_scheduled_at TEXT NOT NULL,
                timezone_label TEXT,
                status TEXT NOT NULL CHECK (status IN ('pending', 'waiting_connection', 'submitted', 'completed', 'failed', 'blocked', 'cancelled')),
                requires_linkedin_connected INTEGER NOT NULL DEFAULT 0,
                cancel_on_gmail_reply INTEGER NOT NULL DEFAULT 0,
                cancel_on_linkedin_connected INTEGER NOT NULL DEFAULT 0,
                openoutreach_campaign_id TEXT,
                openoutreach_public_identifier TEXT,
                openoutreach_deal_id TEXT,
                openoutreach_external_job_id TEXT,
                next_retry_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO linkedin_jobs (
                id, workflow_id, contact_id, step_id, batch_name, kind, linkedin_url, message,
                scheduled_at, requested_scheduled_at, timezone_label, status, requires_linkedin_connected,
                cancel_on_gmail_reply, cancel_on_linkedin_connected, openoutreach_campaign_id,
                openoutreach_public_identifier, openoutreach_deal_id, openoutreach_external_job_id,
                next_retry_at, attempt_count, last_error, created_at, updated_at
            )
            SELECT
                id, workflow_id, contact_id, step_id, batch_name,
                CASE
                    WHEN kind IN ('outreach', 'connection_request', 'message_after_acceptance') THEN kind
                    ELSE 'outreach'
                END,
                linkedin_url, message, scheduled_at, requested_scheduled_at, timezone_label, status,
                COALESCE(requires_linkedin_connected, 0),
                COALESCE(cancel_on_gmail_reply, 0),
                COALESCE(cancel_on_linkedin_connected, 0),
                openoutreach_campaign_id, openoutreach_public_identifier, openoutreach_deal_id,
                openoutreach_external_job_id, next_retry_at, COALESCE(attempt_count, 0), last_error,
                created_at, updated_at
            FROM linkedin_jobs_old
            """
        )
        connection.execute("DROP TABLE linkedin_jobs_old")
        connection.execute("PRAGMA foreign_keys = ON")

    def _migrate_mail_jobs_table(self, connection):
        existing_columns = self._table_columns(connection, "mail_jobs")
        required_columns = {
            "workflow_id",
            "step_id",
            "message_id",
            "gmail_message_id",
            "gmail_thread_id",
            "parent_job_id",
            "root_job_id",
            "reply_stop_enabled",
            "cancel_on_linkedin_connected",
            "blocked_reason",
        }
        current_sql = self._table_sql(connection, "mail_jobs")
        needs_rebuild = not required_columns.issubset(existing_columns) or "'blocked'" not in current_sql
        if not needs_rebuild:
            connection.execute(
                """
                UPDATE mail_jobs
                SET root_job_id = COALESCE(root_job_id, id),
                    reply_stop_enabled = COALESCE(reply_stop_enabled, 1),
                    cancel_on_linkedin_connected = COALESCE(cancel_on_linkedin_connected, 0)
                """
            )
            return

        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("ALTER TABLE mail_jobs RENAME TO mail_jobs_old")
        connection.execute(
            """
            CREATE TABLE mail_jobs (
                id TEXT PRIMARY KEY,
                contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                workflow_id TEXT REFERENCES workflow_runs(id) ON DELETE SET NULL,
                step_id TEXT,
                batch_name TEXT,
                source TEXT NOT NULL DEFAULT 'local_ai',
                subject TEXT NOT NULL,
                body_text TEXT NOT NULL,
                body_html TEXT,
                scheduled_at TEXT NOT NULL,
                requested_scheduled_at TEXT NOT NULL,
                timezone_label TEXT,
                status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'cancelled', 'blocked')),
                attachment_mode TEXT NOT NULL CHECK (attachment_mode IN ('none', 'latest_resume')),
                message_id TEXT,
                gmail_message_id TEXT,
                gmail_thread_id TEXT,
                parent_job_id TEXT,
                root_job_id TEXT,
                reply_stop_enabled INTEGER NOT NULL DEFAULT 1,
                cancel_on_linkedin_connected INTEGER NOT NULL DEFAULT 0,
                blocked_reason TEXT,
                sent_at TEXT,
                failed_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        old_columns = self._table_columns(connection, "mail_jobs_old")
        select_parts = {
            "id": "id",
            "contact_id": "contact_id",
            "workflow_id": "workflow_id" if "workflow_id" in old_columns else "NULL",
            "step_id": "step_id" if "step_id" in old_columns else "NULL",
            "batch_name": "batch_name",
            "source": "source",
            "subject": "subject",
            "body_text": "body_text",
            "body_html": "body_html",
            "scheduled_at": "scheduled_at",
            "requested_scheduled_at": "requested_scheduled_at",
            "timezone_label": "timezone_label",
            "status": "status",
            "attachment_mode": "attachment_mode",
            "message_id": "message_id" if "message_id" in old_columns else "NULL",
            "gmail_message_id": "gmail_message_id" if "gmail_message_id" in old_columns else "NULL",
            "gmail_thread_id": "gmail_thread_id" if "gmail_thread_id" in old_columns else "NULL",
            "parent_job_id": "parent_job_id" if "parent_job_id" in old_columns else "NULL",
            "root_job_id": "COALESCE(root_job_id, id)" if "root_job_id" in old_columns else "id",
            "reply_stop_enabled": "COALESCE(reply_stop_enabled, 1)" if "reply_stop_enabled" in old_columns else "1",
            "cancel_on_linkedin_connected": (
                "COALESCE(cancel_on_linkedin_connected, 0)"
                if "cancel_on_linkedin_connected" in old_columns else "0"
            ),
            "blocked_reason": "blocked_reason" if "blocked_reason" in old_columns else "NULL",
            "sent_at": "sent_at",
            "failed_at": "failed_at",
            "last_error": "last_error",
            "created_at": "created_at",
            "updated_at": "updated_at",
        }
        columns = list(select_parts.keys())
        connection.execute(
            """
            INSERT INTO mail_jobs ({columns})
            SELECT {selects} FROM mail_jobs_old
            """.format(columns=", ".join(columns), selects=", ".join(select_parts[column] for column in columns))
        )
        connection.execute("DROP TABLE mail_jobs_old")
        connection.execute("PRAGMA foreign_keys = ON")

    def _migrate_gmail_auth_table(self, connection):
        existing_columns = self._table_columns(connection, "gmail_auth_state")
        desired_columns = {
            "history_id": "TEXT",
            "last_inbox_sync_at": "TEXT",
            "last_error": "TEXT",
        }
        for column_name, column_type in desired_columns.items():
            if column_name not in existing_columns:
                connection.execute("ALTER TABLE gmail_auth_state ADD COLUMN %s %s" % (column_name, column_type))

    def _migrate_mail_send_attempts_table(self, connection, force: bool = False):
        foreign_keys = connection.execute("PRAGMA foreign_key_list(mail_send_attempts)").fetchall()
        references_old_table = any(row["table"] != "mail_jobs" for row in foreign_keys)
        if not references_old_table and not force:
            return

        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("ALTER TABLE mail_send_attempts RENAME TO mail_send_attempts_old")
        connection.execute(
            """
            CREATE TABLE mail_send_attempts (
                id TEXT PRIMARY KEY,
                mail_job_id TEXT NOT NULL REFERENCES mail_jobs(id) ON DELETE CASCADE,
                attempted_at TEXT NOT NULL,
                success INTEGER NOT NULL,
                error_message TEXT,
                smtp_message_id TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO mail_send_attempts (id, mail_job_id, attempted_at, success, error_message, smtp_message_id)
            SELECT id, mail_job_id, attempted_at, success, error_message, smtp_message_id
            FROM mail_send_attempts_old
            """
        )
        connection.execute("DROP TABLE mail_send_attempts_old")
        connection.execute("PRAGMA foreign_keys = ON")

    def _migrate_inbound_messages_table(self, connection):
        foreign_keys = connection.execute("PRAGMA foreign_key_list(inbound_messages)").fetchall()
        valid_targets = {"contacts", "mail_jobs"}
        references_old_table = any(row["table"] not in valid_targets for row in foreign_keys)
        if not references_old_table:
            return

        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("ALTER TABLE inbound_messages RENAME TO inbound_messages_old")
        connection.execute(
            """
            CREATE TABLE inbound_messages (
                id TEXT PRIMARY KEY,
                contact_id TEXT REFERENCES contacts(id) ON DELETE SET NULL,
                matched_job_id TEXT REFERENCES mail_jobs(id) ON DELETE SET NULL,
                gmail_message_id TEXT,
                gmail_thread_id TEXT,
                message_id TEXT,
                in_reply_to TEXT,
                references_raw TEXT,
                from_email TEXT,
                subject TEXT,
                snippet TEXT,
                received_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO inbound_messages (
                id, contact_id, matched_job_id, gmail_message_id, gmail_thread_id,
                message_id, in_reply_to, references_raw, from_email, subject,
                snippet, received_at, created_at
            )
            SELECT
                id, contact_id, matched_job_id, gmail_message_id, gmail_thread_id,
                message_id, in_reply_to, references_raw, from_email, subject,
                snippet, received_at, created_at
            FROM inbound_messages_old
            """
        )
        connection.execute("DROP TABLE inbound_messages_old")
        connection.execute("PRAGMA foreign_keys = ON")

    def _create_contact_indexes(self, connection):
        connection.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_apollo_person_id
            ON contacts(apollo_person_id)
            WHERE apollo_person_id IS NOT NULL;

            CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_linkedin_key
            ON contacts(linkedin_key)
            WHERE linkedin_key IS NOT NULL;

            CREATE INDEX IF NOT EXISTS idx_contacts_company
            ON contacts(company);

            CREATE INDEX IF NOT EXISTS idx_contacts_title
            ON contacts(title);

            CREATE INDEX IF NOT EXISTS idx_contacts_email_status
            ON contacts(email_status);

            CREATE INDEX IF NOT EXISTS idx_contacts_country_state_city
            ON contacts(country, state, city);

            CREATE INDEX IF NOT EXISTS idx_contacts_last_synced_at
            ON contacts(last_synced_at);
            """
        )

    def _clean_value(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    def _contact_payload(self, **values):
        payload = {key: self._clean_value(value) for key, value in values.items()}
        email = payload.get("email")
        if email:
            payload["email"] = email.lower()
        linkedin_url = payload.get("linkedin_url")
        payload["linkedin_url"] = normalize_linkedin_url(linkedin_url)
        payload["linkedin_key"] = payload["linkedin_url"]
        return payload

    def verify_api_token(self, token: str):
        if not secrets.compare_digest(token, self.settings.api_bearer_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid bearer token",
            )

    def store_file(self, upload: UploadFile, kind: str):
        file_id = str(uuid.uuid4())
        stored_name = "%s_%s" % (file_id, os.path.basename(upload.filename or "upload.bin"))
        stored_path = os.path.join(self.settings.files_dir, stored_name)
        now = to_storage_datetime(utc_now())

        with open(stored_path, "wb") as handle:
            shutil.copyfileobj(upload.file, handle)

        with self.connect() as connection:
            if kind == "resume":
                connection.execute("UPDATE files SET is_active = 0 WHERE kind = ?", (kind,))
                is_active = 1
            else:
                is_active = 1

            connection.execute(
                """
                INSERT INTO files (id, kind, original_name, stored_path, mime_type, created_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    kind,
                    upload.filename or stored_name,
                    stored_path,
                    upload.content_type,
                    now,
                    is_active,
                ),
            )

        return self.get_file(file_id)

    def get_file(self, file_id: str):
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, kind, original_name, stored_path, mime_type, created_at, is_active
                FROM files
                WHERE id = ?
                """,
                (file_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="File not found")
        return dict(row)

    def list_files(self):
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, kind, original_name, stored_path, mime_type, created_at, is_active
                FROM files
                ORDER BY created_at DESC
                """
            ).fetchall()
        files = []
        latest_by_kind = {}
        for row in rows:
            item = dict(row)
            latest_by_kind.setdefault(item["kind"], item["id"])
            item["is_latest_for_kind"] = latest_by_kind[item["kind"]] == item["id"]
            item["is_active"] = bool(item["is_active"])
            files.append(item)
        return files

    def _upsert_contact(
        self,
        connection,
        email: Optional[str],
        name: Optional[str] = None,
        company: Optional[str] = None,
        title: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        source: str = "local_ai",
        apollo_person_id: Optional[str] = None,
        apollo_organization_id: Optional[str] = None,
        email_status: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        headline: Optional[str] = None,
        company_domain: Optional[str] = None,
        city: Optional[str] = None,
        state: Optional[str] = None,
        country: Optional[str] = None,
        formatted_address: Optional[str] = None,
        timezone_name: Optional[str] = None,
        seniority: Optional[str] = None,
        departments_json: Optional[str] = None,
        employment_history_json: Optional[str] = None,
        apollo_raw_json: Optional[str] = None,
    ):
        payload = self._contact_payload(
            apollo_person_id=apollo_person_id,
            apollo_organization_id=apollo_organization_id,
            email=email,
            email_status=email_status,
            name=name,
            first_name=first_name,
            last_name=last_name,
            linkedin_url=linkedin_url,
            title=title,
            headline=headline,
            company=company,
            company_domain=company_domain,
            city=city,
            state=state,
            country=country,
            formatted_address=formatted_address,
            timezone_name=timezone_name,
            seniority=seniority,
            departments_json=departments_json,
            employment_history_json=employment_history_json,
            apollo_raw_json=apollo_raw_json,
            source=source,
        )
        email = payload.get("email")
        apollo_person_id = payload.get("apollo_person_id")
        linkedin_key = payload.get("linkedin_key")
        if email is None and apollo_person_id is None and linkedin_key is None:
            raise HTTPException(
                status_code=400,
                detail="A contact requires at least an email, Apollo person id, or linkedin_url",
            )

        existing = None
        if email is not None:
            existing = connection.execute(
                "SELECT * FROM contacts WHERE lower(email) = ?",
                (email,),
            ).fetchone()
        if existing is None and apollo_person_id is not None:
            existing = connection.execute(
                "SELECT * FROM contacts WHERE apollo_person_id = ?",
                (apollo_person_id,),
            ).fetchone()
        if existing is None and linkedin_key is not None:
            existing = connection.execute(
                "SELECT * FROM contacts WHERE linkedin_key = ?",
                (linkedin_key,),
            ).fetchone()

        now = to_storage_datetime(utc_now())
        if existing is None:
            contact_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO contacts (
                    id, apollo_person_id, apollo_organization_id, email, linkedin_key, email_status, name, first_name,
                    last_name, linkedin_url, title, headline, company, company_domain, city, state, country,
                    formatted_address, timezone_name, seniority, departments_json, employment_history_json,
                    apollo_raw_json, source, has_replied, replied_at, created_at, updated_at, last_synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?)
                """,
                (
                    contact_id,
                    payload.get("apollo_person_id"),
                    payload.get("apollo_organization_id"),
                    email,
                    payload.get("linkedin_key"),
                    payload.get("email_status"),
                    payload.get("name"),
                    payload.get("first_name"),
                    payload.get("last_name"),
                    payload.get("linkedin_url"),
                    payload.get("title"),
                    payload.get("headline"),
                    payload.get("company"),
                    payload.get("company_domain"),
                    payload.get("city"),
                    payload.get("state"),
                    payload.get("country"),
                    payload.get("formatted_address"),
                    payload.get("timezone_name"),
                    payload.get("seniority"),
                    payload.get("departments_json"),
                    payload.get("employment_history_json"),
                    payload.get("apollo_raw_json"),
                    payload.get("source") or "local_ai",
                    now,
                    now,
                    now,
                ),
            )
            return contact_id

        contact_id = existing["id"]
        merged = {}
        for key, value in payload.items():
            if key == "source":
                merged[key] = existing["source"] or value or "local_ai"
            else:
                merged[key] = value if value is not None else existing[key]
        connection.execute(
            """
            UPDATE contacts
            SET apollo_person_id = ?, apollo_organization_id = ?, email = ?, linkedin_key = ?, email_status = ?, name = ?,
                first_name = ?, last_name = ?, linkedin_url = ?, title = ?, headline = ?, company = ?,
                company_domain = ?, city = ?, state = ?, country = ?, formatted_address = ?, timezone_name = ?,
                seniority = ?, departments_json = ?, employment_history_json = ?, apollo_raw_json = ?,
                source = ?, updated_at = ?, last_synced_at = ?
            WHERE id = ?
            """,
            (
                merged["apollo_person_id"],
                merged["apollo_organization_id"],
                merged["email"],
                merged["linkedin_key"],
                merged["email_status"],
                merged["name"],
                merged["first_name"],
                merged["last_name"],
                merged["linkedin_url"],
                merged["title"],
                merged["headline"],
                merged["company"],
                merged["company_domain"],
                merged["city"],
                merged["state"],
                merged["country"],
                merged["formatted_address"],
                merged["timezone_name"],
                merged["seniority"],
                merged["departments_json"],
                merged["employment_history_json"],
                merged["apollo_raw_json"],
                merged["source"],
                now,
                now,
                contact_id,
            ),
        )
        return contact_id

    def _contact_base_query(self):
        return """
            SELECT
                contacts.*,
                COALESCE(stats.total_jobs, 0) AS total_jobs,
                COALESCE(stats.sent_count, 0) AS sent_count,
                COALESCE(stats.pending_count, 0) AS pending_count,
                COALESCE(stats.failed_count, 0) AS failed_count,
                COALESCE(stats.cancelled_count, 0) AS cancelled_count,
                COALESCE(stats.blocked_count, 0) AS blocked_count,
                stats.last_sent_at,
                stats.last_scheduled_at
            FROM contacts
            LEFT JOIN (
                SELECT
                    contact_id,
                    COUNT(*) AS total_jobs,
                    SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent_count,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_count,
                    SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked_count,
                    MAX(sent_at) AS last_sent_at,
                    MAX(scheduled_at) AS last_scheduled_at
                FROM mail_jobs
                GROUP BY contact_id
            ) AS stats ON stats.contact_id = contacts.id
        """

    def list_contacts(
        self,
        company: Optional[str] = None,
        company_mode: str = "any",
        title: Optional[str] = None,
        email: Optional[str] = None,
        name: Optional[str] = None,
        country: Optional[str] = None,
        has_linkedin: Optional[bool] = None,
        has_sent_mail: Optional[bool] = None,
    ):
        clauses = []
        params = []

        if company:
            clauses.append("COALESCE(contacts.company, '') LIKE ?")
            params.append("%%%s%%" % company)
        if company_mode == "present":
            clauses.append("contacts.company IS NOT NULL AND TRIM(contacts.company) <> ''")
        elif company_mode == "missing":
            clauses.append("(contacts.company IS NULL OR TRIM(contacts.company) = '')")
        if title:
            clauses.append("COALESCE(contacts.title, '') LIKE ?")
            params.append("%%%s%%" % title)
        if email:
            clauses.append("COALESCE(contacts.email, '') LIKE ?")
            params.append("%%%s%%" % email.lower())
        if name:
            clauses.append("COALESCE(contacts.name, '') LIKE ?")
            params.append("%%%s%%" % name)
        if country:
            clauses.append("COALESCE(contacts.country, '') LIKE ?")
            params.append("%%%s%%" % country)
        if has_linkedin is True:
            clauses.append("contacts.linkedin_url IS NOT NULL AND TRIM(contacts.linkedin_url) <> ''")
        elif has_linkedin is False:
            clauses.append("(contacts.linkedin_url IS NULL OR TRIM(contacts.linkedin_url) = '')")
        if has_sent_mail is True:
            clauses.append("COALESCE(stats.sent_count, 0) > 0")
        elif has_sent_mail is False:
            clauses.append("COALESCE(stats.sent_count, 0) = 0")

        query = self._contact_base_query()
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += """
            ORDER BY
                CASE WHEN contacts.company IS NULL OR TRIM(contacts.company) = '' THEN 1 ELSE 0 END,
                LOWER(COALESCE(contacts.company, '')),
                LOWER(COALESCE(contacts.name, contacts.email, ''))
        """

        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_contact(self, contact_id: str):
        query = self._contact_base_query() + " WHERE contacts.id = ?"
        with self.connect() as connection:
            row = connection.execute(query, (contact_id,)).fetchone()
            jobs = connection.execute(
                self._job_base_query() + " WHERE mail_jobs.contact_id = ? ORDER BY mail_jobs.scheduled_at DESC, mail_jobs.created_at DESC",
                (contact_id,),
            ).fetchall()
            linkedin_jobs = connection.execute(
                self._linkedin_job_base_query() + " WHERE linkedin_jobs.contact_id = ? ORDER BY linkedin_jobs.scheduled_at DESC, linkedin_jobs.created_at DESC",
                (contact_id,),
            ).fetchall()
            inbound_messages = connection.execute(
                """
                SELECT *
                FROM inbound_messages
                WHERE contact_id = ?
                ORDER BY received_at DESC, created_at DESC
                """,
                (contact_id,),
            ).fetchall()
            workflow_events = connection.execute(
                """
                SELECT workflow_events.*
                FROM workflow_events
                WHERE contact_id = ?
                ORDER BY created_at DESC
                """,
                (contact_id,),
            ).fetchall()
        if row is None:
            raise HTTPException(status_code=404, detail="Contact not found")
        item = dict(row)
        item["has_replied"] = bool(item.get("has_replied"))
        item["mail_jobs"] = [dict(job) for job in jobs]
        item["linkedin_jobs"] = [dict(job) for job in linkedin_jobs]
        item["inbound_messages"] = [dict(message) for message in inbound_messages]
        item["workflow_events"] = [dict(event) for event in workflow_events]
        return item

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
                )
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
                        subject = self._clean_value(step.get("subject"))
                        body_text = self._clean_value(step.get("body_text"))
                        if not subject or not body_text:
                            raise HTTPException(status_code=400, detail="email step requires subject and body_text")
                        parsed = parse_datetime(step["scheduled_at"])
                        job_id = str(uuid.uuid4())
                        parent_step_id = self._clean_value(step.get("thread_parent_step_id"))
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
        now = to_storage_datetime(utc_now())
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM mail_jobs
                WHERE status = 'pending' AND scheduled_at <= ?
                ORDER BY scheduled_at ASC
                """,
                (now,),
            ).fetchall()
        return [row["id"] for row in rows]

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

    def _get_latest_active_resume(self, connection):
        return connection.execute(
            """
            SELECT id, kind, original_name, stored_path, mime_type, created_at, is_active
            FROM files
            WHERE kind = 'resume' AND is_active = 1
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()

    def _record_attempt(self, connection, job_id: str, success: bool, error_message: Optional[str], smtp_message_id: Optional[str]):
        params = (
            str(uuid.uuid4()),
            job_id,
            to_storage_datetime(utc_now()),
            1 if success else 0,
            error_message,
            smtp_message_id,
        )
        sql = """
            INSERT INTO mail_send_attempts (id, mail_job_id, attempted_at, success, error_message, smtp_message_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """
        try:
            connection.execute(sql, params)
        except sqlite3.OperationalError as exc:
            # Some older SQLite migrations can leave mail_send_attempts pointing at
            # the transient mail_jobs_old table. Rebuild the dependency and retry.
            if "mail_jobs_old" not in str(exc):
                raise
            self._migrate_mail_send_attempts_table(connection, force=True)
            connection.execute(sql, params)

    def _gmail_auth_row(self, connection):
        return connection.execute("SELECT * FROM gmail_auth_state WHERE id = 1").fetchone()

    def _store_gmail_auth(
        self,
        connection,
        *,
        email: Optional[str],
        access_token: Optional[str],
        refresh_token: Optional[str],
        expiry: Optional[str],
        scope: Optional[str],
        history_id: Optional[str] = None,
        last_inbox_sync_at: Optional[str] = None,
        last_error: Optional[str] = None,
    ):
        now = to_storage_datetime(utc_now())
        existing = self._gmail_auth_row(connection)
        created_at = existing["created_at"] if existing else now
        connection.execute(
            """
            INSERT INTO gmail_auth_state (
                id, email, access_token, refresh_token, expiry, scope, history_id,
                last_inbox_sync_at, last_error, created_at, updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                email = excluded.email,
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expiry = excluded.expiry,
                scope = excluded.scope,
                history_id = excluded.history_id,
                last_inbox_sync_at = excluded.last_inbox_sync_at,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                email,
                access_token,
                refresh_token,
                expiry,
                scope,
                history_id,
                last_inbox_sync_at,
                last_error,
                created_at,
                now,
            ),
        )

    def get_gmail_auth_status(self):
        with self.connect() as connection:
            row = self._gmail_auth_row(connection)
        if row is None or not row["refresh_token"]:
            return {
                "connected": False,
                "email": None,
                "scope": None,
                "expiry": None,
                "last_inbox_sync_at": None,
                "last_error": None,
                "history_id": None,
            }
        return {
            "connected": True,
            "email": row["email"],
            "scope": row["scope"],
            "expiry": row["expiry"],
            "last_inbox_sync_at": row["last_inbox_sync_at"],
            "last_error": row["last_error"],
            "history_id": row["history_id"],
        }

    def complete_gmail_oauth(self, code: str, redirect_uri: str):
        if not self.settings.gmail_client_id or not self.settings.gmail_client_secret:
            raise HTTPException(status_code=500, detail="Gmail OAuth credentials are not configured")
        tokens = gmail_api.exchange_code_for_tokens(
            self.settings.gmail_client_id,
            self.settings.gmail_client_secret,
            redirect_uri,
            code,
        )
        profile = gmail_api.get_profile(tokens["access_token"])
        scope = tokens.get("scope") or " ".join(gmail_api.GMAIL_SCOPES)
        expiry = None
        if tokens.get("expires_in"):
            expiry = to_storage_datetime(utc_now().replace(microsecond=0) + timedelta(seconds=int(tokens["expires_in"])))
        with self.connect() as connection:
            current = self._gmail_auth_row(connection)
            self._store_gmail_auth(
                connection,
                email=profile.get("emailAddress"),
                access_token=tokens.get("access_token"),
                refresh_token=tokens.get("refresh_token") or (current["refresh_token"] if current else None),
                expiry=expiry,
                scope=scope,
                history_id=current["history_id"] if current else None,
                last_inbox_sync_at=current["last_inbox_sync_at"] if current else None,
                last_error=None,
            )
        return self.get_gmail_auth_status()

    def disconnect_gmail(self):
        with self.connect() as connection:
            connection.execute("DELETE FROM gmail_auth_state WHERE id = 1")

    def _get_valid_gmail_auth(self, connection, *, required: bool):
        row = self._gmail_auth_row(connection)
        if row is None or not row["refresh_token"]:
            if required:
                raise GmailAuthorizationError("Connect Gmail before sending email")
            return None

        expiry = row["expiry"]
        needs_refresh = True
        if row["access_token"] and expiry:
            try:
                expires_at = datetime.fromisoformat(expiry)
                needs_refresh = expires_at <= utc_now()
            except ValueError:
                needs_refresh = True

        if not row["access_token"]:
            needs_refresh = True

        if not needs_refresh:
            return dict(row)

        try:
            refreshed = gmail_api.refresh_access_token(
                self.settings.gmail_client_id,
                self.settings.gmail_client_secret,
                row["refresh_token"],
            )
        except Exception as exc:
            error_message = "Gmail authorization expired. Reconnect Gmail. %s" % exc
            connection.execute(
                """
                UPDATE gmail_auth_state
                SET last_error = ?, updated_at = ?
                WHERE id = 1
                """,
                (error_message, to_storage_datetime(utc_now())),
            )
            raise GmailAuthorizationError(error_message) from exc

        expiry = None
        if refreshed.get("expires_in"):
            expiry = to_storage_datetime(utc_now().replace(microsecond=0) + timedelta(seconds=int(refreshed["expires_in"])))
        self._store_gmail_auth(
            connection,
            email=row["email"],
            access_token=refreshed.get("access_token"),
            refresh_token=row["refresh_token"],
            expiry=expiry,
            scope=refreshed.get("scope") or row["scope"],
            history_id=row["history_id"],
            last_inbox_sync_at=row["last_inbox_sync_at"],
            last_error=None,
        )
        refreshed_row = self._gmail_auth_row(connection)
        return dict(refreshed_row)

    def _sender_identity(self, auth_row: Optional[Dict]):
        return self.settings.mail_from or (auth_row or {}).get("email")

    def _generate_message_id(self, job_id: str, sender_email: str):
        domain = (sender_email or "localhost").split("@")[-1]
        return "<mailtfoutofit.%s@%s>" % (job_id, domain)

    def _attachment_for_job(self, connection, job_row: Dict):
        if job_row["attachment_mode"] != "latest_resume":
            return None
        attachment_row = self._get_latest_active_resume(connection)
        if attachment_row is None:
            raise FileNotFoundError("No active resume is available on the server")
        return dict(attachment_row)

    def _load_job_row(self, connection, job_id: str):
        row = connection.execute(self._job_base_query() + " WHERE mail_jobs.id = ?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Mail job not found")
        return dict(row)

    def _resolve_reply_context(self, connection, job_row: Dict):
        parent_job_id = job_row.get("parent_job_id")
        if not parent_job_id:
            return {"parent_message_id": None, "references": [], "thread_id": None}

        references = []
        latest_sent_ancestor = None
        thread_id = None
        visited = set()
        current_id = parent_job_id
        while current_id and current_id not in visited:
            visited.add(current_id)
            ancestor_row = connection.execute(
                """
                SELECT id, parent_job_id, message_id, gmail_thread_id, sent_at
                FROM mail_jobs
                WHERE id = ?
                """,
                (current_id,),
            ).fetchone()
            if ancestor_row is None:
                break
            ancestor = dict(ancestor_row)
            if ancestor.get("message_id"):
                references.append(ancestor["message_id"])
            if latest_sent_ancestor is None and ancestor.get("message_id") and ancestor.get("sent_at"):
                latest_sent_ancestor = ancestor
            if thread_id is None and ancestor.get("gmail_thread_id"):
                thread_id = ancestor["gmail_thread_id"]
            current_id = ancestor.get("parent_job_id")

        references.reverse()
        return {
            "parent_message_id": latest_sent_ancestor["message_id"] if latest_sent_ancestor else (references[-1] if references else None),
            "references": references,
            "thread_id": thread_id,
        }

    def _build_email_message(
        self,
        job_row: Dict,
        attachment: Optional[Dict],
        sender_email: str,
        message_id: str,
        reply_context: Dict,
    ):
        message = EmailMessage()
        message["From"] = sender_email
        message["To"] = job_row["recipient_email"]
        message["Subject"] = job_row["subject"]
        message["Message-ID"] = message_id
        if reply_context.get("parent_message_id"):
            message["In-Reply-To"] = reply_context["parent_message_id"]
        if reply_context.get("references"):
            message["References"] = " ".join(reply_context["references"])

        message.set_content(job_row["body_text"])
        if job_row.get("body_html"):
            message.add_alternative(job_row["body_html"], subtype="html")

        if attachment is not None:
            with open(attachment["stored_path"], "rb") as handle:
                data = handle.read()
            mime_type = attachment["mime_type"] or "application/octet-stream"
            maintype, subtype = mime_type.split("/", 1) if "/" in mime_type else ("application", "octet-stream")
            message.add_attachment(
                data,
                maintype=maintype,
                subtype=subtype,
                filename=attachment["original_name"],
            )
        return message

    def _deliver_job(self, job_row: Dict, attachment: Optional[Dict], auth_row: Dict, message_id: str, reply_context: Dict):
        if self.settings.mail_provider != "gmail_api":
            raise RuntimeError("Unsupported mail provider: %s" % self.settings.mail_provider)
        sender_email = self._sender_identity(auth_row)
        if not sender_email:
            raise RuntimeError("Gmail sender email is not configured")
        message = self._build_email_message(job_row, attachment, sender_email, message_id, reply_context)
        response = gmail_api.send_message(
            auth_row["access_token"],
            message.as_bytes(),
            thread_id=reply_context.get("thread_id"),
        )
        return {
            "message_id": message_id,
            "gmail_message_id": response.get("id"),
            "gmail_thread_id": response.get("threadId"),
        }

    def _thread_has_reply(self, connection, job_row: Dict):
        root_job_id = job_row.get("root_job_id") or job_row["id"]
        reply_row = connection.execute(
            """
            SELECT 1
            FROM inbound_messages
            JOIN mail_jobs matched_jobs ON matched_jobs.id = inbound_messages.matched_job_id
            WHERE inbound_messages.contact_id = ?
              AND matched_jobs.root_job_id = ?
            LIMIT 1
            """,
            (job_row["contact_id"], root_job_id),
        ).fetchone()
        return reply_row is not None

    def send_mail_job(self, job_id: str):
        job = self.get_mail_job(job_id)
        if job["status"] not in {"pending", "failed"}:
            raise HTTPException(status_code=400, detail="Only pending or failed jobs can be sent")

        should_refresh_replies = (
            self.settings.mail_provider == "gmail_api"
            and job.get("reply_stop_enabled")
            and job.get("parent_job_id")
        )
        if should_refresh_replies:
            try:
                self.sync_inbound_replies()
            except Exception:
                pass

        with self.connect() as connection:
            job_row = self._load_job_row(connection, job_id)
            if job_row["status"] not in {"pending", "failed"}:
                return self.get_mail_job(job_id)
            if job_row.get("reply_stop_enabled") and job_row.get("parent_job_id"):
                try:
                    self._refresh_thread_replies(connection, job_row)
                    job_row = self._load_job_row(connection, job_id)
                except Exception:
                    pass
            skip_send = False
            if job_row.get("reply_stop_enabled") and self._thread_has_reply(connection, job_row):
                blocked_message = "Follow-up blocked because the contact already replied"
                connection.execute(
                    """
                    UPDATE mail_jobs
                    SET status = 'blocked', blocked_reason = 'contact_replied', updated_at = ?
                    WHERE id = ?
                    """,
                    (to_storage_datetime(utc_now()), job_id),
                )
                self._record_attempt(connection, job_id, False, blocked_message, None)
                skip_send = True

            if not skip_send:
                try:
                    attachment = self._attachment_for_job(connection, job_row)
                except FileNotFoundError as exc:
                    error_message = str(exc)
                    connection.execute(
                        """
                        UPDATE mail_jobs
                        SET status = 'failed', failed_at = ?, last_error = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            to_storage_datetime(utc_now()),
                            error_message,
                            to_storage_datetime(utc_now()),
                            job_id,
                        ),
                    )
                    self._record_attempt(connection, job_id, False, error_message, None)
                    skip_send = True

            if not skip_send:
                try:
                    auth_row = self._get_valid_gmail_auth(connection, required=True)
                    sender_email = self._sender_identity(auth_row)
                    message_id = job_row.get("message_id") or self._generate_message_id(job_id, sender_email or "gmail.local")
                    reply_context = self._resolve_reply_context(connection, job_row)
                    delivery = self._deliver_job(job_row, attachment, auth_row, message_id, reply_context)
                except GmailAuthorizationError as exc:
                    error_message = str(exc)
                    connection.execute(
                        """
                        UPDATE mail_jobs
                        SET status = 'failed', failed_at = ?, last_error = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            to_storage_datetime(utc_now()),
                            error_message,
                            to_storage_datetime(utc_now()),
                            job_id,
                        ),
                    )
                    self._record_attempt(connection, job_id, False, error_message, None)
                except Exception as exc:
                    error_message = str(exc)
                    connection.execute(
                        """
                        UPDATE mail_jobs
                        SET status = 'failed', failed_at = ?, last_error = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            to_storage_datetime(utc_now()),
                            error_message,
                            to_storage_datetime(utc_now()),
                            job_id,
                        ),
                    )
                    self._record_attempt(connection, job_id, False, error_message, None)
                else:
                    connection.execute(
                        """
                        UPDATE mail_jobs
                        SET status = 'sent',
                            sent_at = ?,
                            failed_at = NULL,
                            last_error = NULL,
                            message_id = ?,
                            gmail_message_id = ?,
                            gmail_thread_id = COALESCE(?, gmail_thread_id),
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            to_storage_datetime(utc_now()),
                            delivery["message_id"],
                            delivery["gmail_message_id"],
                            delivery["gmail_thread_id"],
                            to_storage_datetime(utc_now()),
                            job_id,
                        ),
                    )
                    self._record_attempt(connection, job_id, True, None, delivery["gmail_message_id"])

        return self.get_mail_job(job_id)

    def _refresh_thread_replies(self, connection, job_row: Dict):
        if self.settings.mail_provider != "gmail_api":
            return 0

        reply_context = self._resolve_reply_context(connection, job_row)
        thread_id = reply_context.get("thread_id") or job_row.get("gmail_thread_id")
        if not thread_id:
            return 0

        auth_row = self._get_valid_gmail_auth(connection, required=True)
        try:
            payload = gmail_api.get_thread(auth_row["access_token"], thread_id)
        except gmail_api.GmailApiError as exc:
            if _is_gmail_not_found_error(exc):
                return 0
            raise

        processed_count = 0
        for message in payload.get("messages", []):
            if self._process_inbound_message(connection, message):
                processed_count += 1
        return processed_count

    def _block_followups(self, connection, matched_job: Dict):
        root_job_id = matched_job.get("root_job_id") or matched_job["id"]
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'blocked',
                blocked_reason = 'contact_replied',
                updated_at = ?
            WHERE root_job_id = ?
              AND contact_id = ?
              AND status = 'pending'
              AND reply_stop_enabled = 1
              AND id != ?
            """,
            (
                to_storage_datetime(utc_now()),
                root_job_id,
                matched_job["contact_id"],
                matched_job["id"],
            ),
        )

    def _match_inbound_job(self, connection, payload: Dict):
        in_reply_to = _first_header_value(payload, "In-Reply-To")
        references_raw = _first_header_value(payload, "References")
        candidate_ids = []
        candidate_ids.extend(_message_ids_from_header(in_reply_to))
        candidate_ids.extend(_message_ids_from_header(references_raw))

        matched_job_row = None
        for candidate in candidate_ids:
            matched_job_row = connection.execute(
                """
                SELECT id, contact_id, root_job_id, workflow_id
                FROM mail_jobs
                WHERE message_id = ?
                LIMIT 1
                """,
                (candidate,),
            ).fetchone()
            if matched_job_row is not None:
                break

        if matched_job_row is None and payload.get("threadId"):
            matched_job_row = connection.execute(
                """
                SELECT id, contact_id, root_job_id, workflow_id
                FROM mail_jobs
                WHERE gmail_thread_id = ?
                ORDER BY sent_at DESC, created_at DESC
                LIMIT 1
                """,
                (payload.get("threadId"),),
            ).fetchone()

        return dict(matched_job_row) if matched_job_row is not None else None

    def _process_inbound_message(self, connection, payload: Dict):
        gmail_message_id = payload.get("id")
        if not gmail_message_id:
            return False
        existing_row = connection.execute(
            "SELECT id, matched_job_id, contact_id FROM inbound_messages WHERE gmail_message_id = ?",
            (gmail_message_id,),
        ).fetchone()

        label_ids = payload.get("labelIds", [])
        if "SENT" in label_ids:
            return False

        message_id = _first_header_value(payload, "Message-ID")
        in_reply_to = _first_header_value(payload, "In-Reply-To")
        references_raw = _first_header_value(payload, "References")
        from_email = parseaddr(_first_header_value(payload, "From") or "")[1].lower() or None
        subject = _first_header_value(payload, "Subject")
        internal_date = payload.get("internalDate")
        if internal_date:
            received_at = datetime.fromtimestamp(int(internal_date) / 1000, UTC).isoformat()
        else:
            received_at = to_storage_datetime(utc_now())

        matched_job = self._match_inbound_job(connection, payload)
        contact_id = matched_job["contact_id"] if matched_job else None
        now = to_storage_datetime(utc_now())

        if existing_row is None:
            connection.execute(
                """
                INSERT INTO inbound_messages (
                    id, contact_id, matched_job_id, gmail_message_id, gmail_thread_id, message_id,
                    in_reply_to, references_raw, from_email, subject, snippet, received_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    contact_id,
                    matched_job["id"] if matched_job else None,
                    gmail_message_id,
                    payload.get("threadId"),
                    message_id,
                    in_reply_to,
                    references_raw,
                    from_email,
                    subject,
                    payload.get("snippet"),
                    received_at,
                    now,
                ),
            )
        elif existing_row["matched_job_id"] is None and matched_job is not None:
            connection.execute(
                """
                UPDATE inbound_messages
                SET contact_id = ?,
                    matched_job_id = ?,
                    gmail_thread_id = COALESCE(?, gmail_thread_id),
                    message_id = COALESCE(?, message_id),
                    in_reply_to = COALESCE(?, in_reply_to),
                    references_raw = COALESCE(?, references_raw),
                    from_email = COALESCE(?, from_email),
                    subject = COALESCE(?, subject),
                    snippet = COALESCE(?, snippet),
                    received_at = COALESCE(?, received_at)
                WHERE id = ?
                """,
                (
                    matched_job["contact_id"],
                    matched_job["id"],
                    payload.get("threadId"),
                    message_id,
                    in_reply_to,
                    references_raw,
                    from_email,
                    subject,
                    payload.get("snippet"),
                    received_at,
                    existing_row["id"],
                ),
            )
        elif existing_row is not None:
            return False

        if matched_job:
            connection.execute(
                """
                UPDATE contacts
                SET has_replied = 1,
                    replied_at = COALESCE(replied_at, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (received_at, now, matched_job["contact_id"]),
            )
            self._block_followups(connection, matched_job)
            self._block_workflow_jobs_for_gmail_reply(connection, matched_job)
            return True
        return False

    def sync_inbound_replies(self):
        with self.connect() as connection:
            try:
                auth_row = self._get_valid_gmail_auth(connection, required=False)
            except GmailAuthorizationError:
                return {"connected": False, "processed_count": 0}
            if auth_row is None:
                return {"connected": False, "processed_count": 0}
            access_token = auth_row["access_token"]
            previous_history_id = auth_row["history_id"]

        processed_count = 0
        highest_history_id = previous_history_id

        try:
            metadata_messages = []
            if previous_history_id:
                try:
                    history_payload = gmail_api.list_history(access_token, previous_history_id)
                except gmail_api.GmailApiError as exc:
                    if not _is_gmail_not_found_error(exc):
                        raise
                    previous_history_id = None
                    highest_history_id = None
                    history_payload = None
                if history_payload is not None:
                    if isinstance(history_payload, dict):
                        history_entries = history_payload.get("history", [])
                        highest_history_id = _max_history_id(highest_history_id, history_payload.get("historyId"))
                    else:
                        history_entries = history_payload or []
                    message_ids = []
                    for entry in history_entries:
                        highest_history_id = _max_history_id(highest_history_id, entry.get("id"))
                        for added in entry.get("messagesAdded", []):
                            message = added.get("message") or {}
                            if message.get("id"):
                                message_ids.append(message["id"])
                    for message_id in dict.fromkeys(message_ids):
                        try:
                            metadata = gmail_api.get_message_metadata(access_token, message_id)
                        except gmail_api.GmailApiError as exc:
                            if _is_gmail_not_found_error(exc):
                                continue
                            raise
                        metadata_messages.append(metadata)

            if not previous_history_id:
                recent_payload = gmail_api.list_recent_messages(access_token, max_results=50)
                if isinstance(recent_payload, dict):
                    recent_messages = recent_payload.get("messages", [])
                else:
                    recent_messages = recent_payload or []
                metadata_messages = []
                for item in recent_messages:
                    try:
                        metadata = gmail_api.get_message_metadata(access_token, item["id"])
                    except gmail_api.GmailApiError as exc:
                        if _is_gmail_not_found_error(exc):
                            continue
                        raise
                    metadata_messages.append(metadata)
                    if metadata.get("historyId"):
                        highest_history_id = _max_history_id(highest_history_id, metadata["historyId"])

            # Always rescan a small recent inbox window as a safety net in case
            # a reply was previously missed due to matcher changes or partial sync state.
            recent_payload = gmail_api.list_recent_messages(access_token, max_results=25)
            if isinstance(recent_payload, dict):
                recent_messages = recent_payload.get("messages", [])
            else:
                recent_messages = recent_payload or []
            seen_ids = {payload.get("id") for payload in metadata_messages if payload.get("id")}
            for item in recent_messages:
                message_id = item.get("id")
                if not message_id or message_id in seen_ids:
                    continue
                try:
                    metadata = gmail_api.get_message_metadata(access_token, message_id)
                except gmail_api.GmailApiError as exc:
                    if _is_gmail_not_found_error(exc):
                        continue
                    raise
                metadata_messages.append(metadata)
                seen_ids.add(message_id)
                if metadata.get("historyId"):
                    highest_history_id = _max_history_id(highest_history_id, metadata["historyId"])
        except Exception as exc:
            with self.connect() as connection:
                row = self._gmail_auth_row(connection)
                if row is not None:
                    self._store_gmail_auth(
                        connection,
                        email=row["email"],
                        access_token=row["access_token"],
                        refresh_token=row["refresh_token"],
                        expiry=row["expiry"],
                        scope=row["scope"],
                        history_id=None,
                        last_inbox_sync_at=row["last_inbox_sync_at"],
                        last_error=str(exc),
                    )
            return {"connected": True, "processed_count": 0, "error": str(exc)}

        with self.connect() as connection:
            for payload in metadata_messages:
                if payload.get("historyId"):
                    highest_history_id = _max_history_id(highest_history_id, payload["historyId"])
                if self._process_inbound_message(connection, payload):
                    processed_count += 1

            row = self._gmail_auth_row(connection)
            if row is not None:
                self._store_gmail_auth(
                    connection,
                    email=row["email"],
                    access_token=row["access_token"],
                    refresh_token=row["refresh_token"],
                    expiry=row["expiry"],
                    scope=row["scope"],
                    history_id=highest_history_id or row["history_id"],
                    last_inbox_sync_at=to_storage_datetime(utc_now()),
                    last_error=None,
                )

        return {"connected": True, "processed_count": processed_count, "history_id": highest_history_id}

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

    def _block_workflow_jobs_for_gmail_reply(self, connection, matched_job: Dict):
        workflow_id = matched_job.get("workflow_id")
        if not workflow_id:
            return
        self._record_workflow_event(
            connection,
            workflow_id,
            matched_job["contact_id"],
            "gmail_replied",
            {"matched_job_id": matched_job["id"]},
        )
        connection.execute(
            """
            UPDATE linkedin_jobs
            SET status = 'blocked',
                last_error = 'Blocked because the contact replied by email',
                updated_at = ?
            WHERE workflow_id = ?
              AND (
                    (kind = 'message_after_acceptance')
                    OR (kind = 'outreach')
                  )
              AND cancel_on_gmail_reply = 1
              AND status IN ('pending', 'waiting_connection', 'failed')
            """,
            (to_storage_datetime(utc_now()), workflow_id),
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

    def _openoutreach_headers(self):
        token = self.settings.openoutreach_api_token
        if not self.settings.openoutreach_base_url or not token:
            raise RuntimeError("OpenOutreach integration is not configured")
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _openoutreach_request(self, method: str, path: str, *, json_body: Optional[Dict] = None):
        url = self.settings.openoutreach_base_url.rstrip("/") + path
        with httpx.Client(timeout=30.0) as client:
            response = client.request(method, url, headers=self._openoutreach_headers(), json=json_body)
        if response.status_code >= 400:
            raise RuntimeError(f"OpenOutreach {method} {path} failed: {response.status_code} {response.text}")
        return response.json()

    def _ensure_openoutreach_daemon(self):
        if not self.settings.openoutreach_auto_start_daemon:
            return
        status_payload = self._openoutreach_request("GET", "/api/manual/daemon/status")
        if not status_payload.get("running"):
            self._openoutreach_request("POST", "/api/manual/daemon/start")

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


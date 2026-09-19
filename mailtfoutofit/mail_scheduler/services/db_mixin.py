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




class DBMixin:
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

                CREATE TABLE IF NOT EXISTS job_applications (
                    id TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    role TEXT,
                    job_url TEXT,
                    job_board TEXT,
                    status TEXT NOT NULL DEFAULT 'Applied',
                    notes TEXT,
                    applied_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
                    number TEXT,
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
                    last_synced_at TEXT,
                    job_application_id TEXT REFERENCES job_applications(id) ON DELETE SET NULL
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
                    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'cancelled', 'blocked', 'gmail_scheduled')),
                    attachment_mode TEXT NOT NULL CHECK (attachment_mode IN ('none', 'latest_resume')),
                    message_id TEXT,
                    gmail_message_id TEXT,
                    gmail_thread_id TEXT,
                    gmail_draft_id TEXT,
                    gmail_scheduled_at TEXT,
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
                    last_synced_at TEXT,
                    job_application_id TEXT REFERENCES job_applications(id) ON DELETE SET NULL
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
            "job_application_id": "TEXT REFERENCES job_applications(id) ON DELETE SET NULL",
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
        needs_rebuild = (
            not required_columns.issubset(existing_columns)
            or "'blocked'" not in current_sql
            or "'gmail_scheduled'" not in current_sql
            or "gmail_draft_id" not in existing_columns
        )
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
                status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'cancelled', 'blocked', 'gmail_scheduled')),
                attachment_mode TEXT NOT NULL CHECK (attachment_mode IN ('none', 'latest_resume')),
                message_id TEXT,
                gmail_message_id TEXT,
                gmail_thread_id TEXT,
                gmail_draft_id TEXT,
                gmail_scheduled_at TEXT,
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
            "gmail_draft_id": "gmail_draft_id" if "gmail_draft_id" in old_columns else "NULL",
            "gmail_scheduled_at": "gmail_scheduled_at" if "gmail_scheduled_at" in old_columns else "NULL",
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


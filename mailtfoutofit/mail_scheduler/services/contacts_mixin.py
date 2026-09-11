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




class ContactsMixin:
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
        job_application_id: Optional[str] = None,
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
            job_application_id=job_application_id,
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
                    apollo_raw_json, source, has_replied, replied_at, created_at, updated_at, last_synced_at, job_application_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?)
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
                    payload.get("job_application_id"),
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
                source = ?, updated_at = ?, last_synced_at = ?, job_application_id = ?
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
                merged["job_application_id"],
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
                COALESCE(stats.gmail_scheduled_count, 0) AS gmail_scheduled_count,
                COALESCE(stats.failed_count, 0) AS failed_count,
                COALESCE(stats.cancelled_count, 0) AS cancelled_count,
                COALESCE(stats.blocked_count, 0) AS blocked_count,
                stats.last_sent_at,
                stats.last_scheduled_at,
                stats.first_scheduled_at,
                stats.next_scheduled_at
            FROM contacts
            LEFT JOIN (
                SELECT
                    mj.contact_id,
                    COUNT(*) AS total_jobs,
                    SUM(CASE WHEN mj.status = 'sent' THEN 1 ELSE 0 END) AS sent_count,
                    SUM(CASE WHEN mj.status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN mj.status = 'gmail_scheduled' THEN 1 ELSE 0 END) AS gmail_scheduled_count,
                    SUM(CASE WHEN mj.status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                    SUM(CASE WHEN mj.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_count,
                    SUM(CASE WHEN mj.status = 'blocked' THEN 1 ELSE 0 END) AS blocked_count,
                    MAX(mj.sent_at) AS last_sent_at,
                    MAX(mj.scheduled_at) AS last_scheduled_at,
                    MIN(mj.scheduled_at) AS first_scheduled_at,
                    MIN(CASE WHEN mj.status IN ('pending', 'gmail_scheduled') THEN mj.scheduled_at ELSE NULL END) AS next_scheduled_at
                FROM mail_jobs mj
                -- Only count jobs from the most recent workflow for this contact.
                -- If a contact was submitted in multiple campaigns, we show the latest one only.
                INNER JOIN (
                    SELECT contact_id, MAX(created_at) AS latest_created_at
                    FROM workflow_runs
                    GROUP BY contact_id
                ) latest_wf ON latest_wf.contact_id = mj.contact_id
                LEFT JOIN workflow_runs wr ON wr.id = mj.workflow_id
                WHERE
                    -- Include jobs that either belong to the latest workflow
                    -- or have no workflow (created via direct /jobs endpoint)
                    mj.workflow_id IS NULL
                    OR wr.created_at = latest_wf.latest_created_at
                GROUP BY mj.contact_id
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

    def mark_contact_replied(self, contact_id: str) -> Dict:
        """Mark a contact as having replied, and cancel all pending/gmail_scheduled follow-ups."""
        now = to_storage_datetime(utc_now())
        cancelled_jobs = []

        with self.connect() as connection:
            # Mark the contact
            connection.execute(
                """
                UPDATE contacts
                SET has_replied = 1, replied_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, contact_id),
            )

            # Find all non-terminal follow-up jobs (excluding root/first emails)
            rows = connection.execute(
                """
                SELECT id, status, parent_job_id
                FROM mail_jobs
                WHERE contact_id = ?
                  AND status IN ('pending', 'gmail_scheduled')
                  AND parent_job_id IS NOT NULL
                ORDER BY scheduled_at ASC
                """,
                (contact_id,),
            ).fetchall()

            for row in rows:
                job_id = row["id"]
                if row["status"] == "gmail_scheduled":
                    self.cancel_gmail_draft_for_job(connection, job_id)
                else:
                    connection.execute(
                        """
                        UPDATE mail_jobs
                        SET status = 'cancelled',
                            blocked_reason = 'contact_replied_manual',
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (now, job_id),
                    )
                cancelled_jobs.append(job_id)

        return {"contact_id": contact_id, "cancelled_jobs": len(cancelled_jobs)}

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


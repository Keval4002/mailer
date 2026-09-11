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




class InboundMixin:
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


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




class SenderMixin:
    def push_upcoming_jobs_to_gmail(self) -> int:
        """On server startup, clean up any orphaned Gmail drafts from previous failed
        scheduled-send attempts, and reset those jobs back to 'pending'.

        Gmail's public REST API v1 does not support scheduled-send (deliveryTime) for
        personal accounts. Drafts can be created but cannot be scheduled via the API.
        Scheduling is handled by the server's scheduler_loop instead.

        Returns the number of orphaned drafts cleaned up.
        """
        cleaned = 0
        with self.connect() as connection:
            auth_row = self._get_valid_gmail_auth(connection, required=False)

            # Find any jobs stuck in gmail_scheduled (from a previous failed attempt)
            rows = connection.execute(
                """
                SELECT id, gmail_draft_id FROM mail_jobs
                WHERE status = 'gmail_scheduled'
                """,
            ).fetchall()

            for row in rows:
                job_id = row["id"]
                draft_id = row["gmail_draft_id"]
                # Delete the orphaned draft from Gmail if we have auth
                if draft_id and auth_row:
                    try:
                        gmail_api.cancel_scheduled_draft(auth_row["access_token"], draft_id)
                        print(f"[Startup] Deleted orphaned Gmail draft {draft_id[:12]}… for job {job_id[:8]}…")
                    except Exception as exc:
                        print(f"[Startup] Could not delete draft {draft_id[:12]}…: {exc}")
                # Reset the job to pending
                connection.execute(
                    """
                    UPDATE mail_jobs
                    SET status = 'pending',
                        gmail_draft_id = NULL,
                        gmail_scheduled_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (to_storage_datetime(utc_now()), job_id),
                )
                cleaned += 1

        if cleaned:
            print(f"[Startup] Reset {cleaned} gmail_scheduled job(s) back to pending.")
        return cleaned

    def cancel_gmail_draft_for_job(self, connection, job_id: str) -> bool:
        """Cancel a gmail_scheduled job by deleting its Gmail draft.

        Returns True if cancelled, False if job was not gmail_scheduled.
        """
        job_row = self._load_job_row(connection, job_id)
        if job_row["status"] != "gmail_scheduled":
            return False

        draft_id = job_row.get("gmail_draft_id")
        if draft_id:
            try:
                auth_row = self._get_valid_gmail_auth(connection, required=False)
                if auth_row:
                    gmail_api.cancel_scheduled_draft(auth_row["access_token"], draft_id)
            except Exception as exc:
                print(f"[Cancel] Failed to delete Gmail draft {draft_id}: {exc}")

        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'cancelled',
                blocked_reason = 'contact_replied_manual',
                updated_at = ?
            WHERE id = ?
            """,
            (to_storage_datetime(utc_now()), job_id),
        )
        return True

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

            # Auto-retry: if any jobs failed due to auth expiration, reset them to pending
            now_str = to_storage_datetime(utc_now())
            connection.execute(
                """
                UPDATE mail_jobs
                SET status = 'pending', updated_at = ?
                WHERE status = 'failed' 
                  AND (
                      error_message LIKE '%gmail authorization expired%' 
                      OR error_message LIKE '%invalid_grant%' 
                      OR error_message LIKE '%token has been expired%'
                      OR error_message LIKE '%connect gmail before sending email%'
                  )
                """,
                (now_str,)
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

            # Double-send paranoia check
            if not skip_send and job_row.get("workflow_id") and job_row.get("step_id"):
                duplicate = connection.execute(
                    """
                    SELECT id FROM mail_jobs 
                    WHERE contact_id = ? AND workflow_id = ? AND step_id = ? AND status = 'sent'
                    """,
                    (job_row["contact_id"], job_row["workflow_id"], job_row["step_id"])
                ).fetchone()
                if duplicate:
                    blocked_message = "Job blocked because this exact step was already sent successfully"
                    connection.execute(
                        """
                        UPDATE mail_jobs
                        SET status = 'blocked', blocked_reason = 'already_sent', updated_at = ?
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


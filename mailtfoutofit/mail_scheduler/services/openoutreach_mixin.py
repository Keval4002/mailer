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




class OpenOutreachMixin:
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


import sqlite3
from io import BytesIO

from fastapi.testclient import TestClient

from mail_scheduler.app import create_app
from mail_scheduler.config import Settings
import mail_scheduler.service as service_module


def build_client(tmp_path):
    settings = Settings(
        api_bearer_token="test-token",
        database_path=str(tmp_path / "test.db"),
        files_dir=str(tmp_path / "files"),
        mail_provider="gmail_api",
        mail_from="user@example.com",
        gmail_client_id="client-id",
        gmail_client_secret="client-secret",
        gmail_redirect_uri="http://testserver/auth/gmail/callback",
        gmail_poll_seconds=60,
        gmail_watch_enabled=False,
        scheduler_poll_seconds=60,
        scheduler_enabled=False,
    )
    app = create_app(settings)
    return app, TestClient(app)


def auth_headers():
    return {"Authorization": "Bearer test-token"}


def connect_gmail(app, email="user@example.com"):
    with app.state.service.connect() as connection:
        app.state.service._store_gmail_auth(
            connection,
            email=email,
            access_token="access-token",
            refresh_token="refresh-token",
            expiry="2099-01-01T00:00:00+00:00",
            scope="gmail.send gmail.readonly",
            history_id=None,
            last_inbox_sync_at=None,
            last_error=None,
        )


def test_api_requires_bearer_token(tmp_path):
    _, client = build_client(tmp_path)
    response = client.get("/api/files")
    assert response.status_code == 401


def test_upload_and_bulk_create(tmp_path):
    _, client = build_client(tmp_path)

    upload_response = client.post(
        "/api/files",
        headers=auth_headers(),
        files={"file": ("resume.pdf", BytesIO(b"resume-data"), "application/pdf")},
        data={"kind": "resume"},
    )
    assert upload_response.status_code == 200
    assert upload_response.json()["kind"] == "resume"
    assert upload_response.json()["is_active"] == 1

    create_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "batch_name": "batch-1",
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "company": "Amazon",
                    "title": "Recruiter",
                    "linkedin_url": "https://linkedin.com/in/person",
                    "subject": "Hello",
                    "body_text": "Body",
                    "body_html": "<p>Body</p>",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": True,
                },
                {
                    "recipient_email": "other@example.com",
                    "subject": "Follow up",
                    "body_text": "Second body",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                },
            ],
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["created_count"] == 2

    list_response = client.get("/api/mail-jobs", headers=auth_headers())
    assert list_response.status_code == 200
    jobs = list_response.json()["mail_jobs"]
    assert len(jobs) == 2
    assert jobs[0]["attachment_mode"] in {"none", "latest_resume"}

    contacts_response = client.get("/api/contacts", headers=auth_headers())
    assert contacts_response.status_code == 200
    contacts = contacts_response.json()["contacts"]
    assert len(contacts) == 2
    assert contacts[0]["email"]


def test_send_now_marks_sent_and_records_attempt(tmp_path):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    client.post(
        "/api/files",
        headers=auth_headers(),
        files={"file": ("resume.pdf", BytesIO(b"resume-data"), "application/pdf")},
        data={"kind": "resume"},
    )

    create_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "subject": "Hello",
                    "body_text": "Body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": True,
                }
            ]
        },
    )
    job_id = create_response.json()["job_ids"][0]

    captured = {}

    def fake_deliver(job, attachment, auth_row, message_id, reply_context):
        captured["attachment_name"] = attachment["original_name"]
        captured["recipient"] = job["recipient_email"]
        captured["reply_context"] = reply_context
        captured["message_id"] = message_id
        return {
            "message_id": message_id,
            "gmail_message_id": "gmail-message-id",
            "gmail_thread_id": "gmail-thread-id",
        }

    app.state.service._deliver_job = fake_deliver

    send_response = client.post("/api/mail-jobs/%s/send-now" % job_id, headers=auth_headers())
    assert send_response.status_code == 200
    assert send_response.json()["status"] == "sent"
    assert send_response.json()["attempts"][0]["smtp_message_id"] == "gmail-message-id"
    assert captured["attachment_name"] == "resume.pdf"
    assert captured["message_id"].startswith("<mailtfoutofit.")


def test_send_now_without_resume_fails_cleanly(tmp_path):
    _, client = build_client(tmp_path)

    create_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "subject": "Hello",
                    "body_text": "Body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": True,
                }
            ]
        },
    )
    job_id = create_response.json()["job_ids"][0]

    send_response = client.post("/api/mail-jobs/%s/send-now" % job_id, headers=auth_headers())
    assert send_response.status_code == 200
    assert send_response.json()["status"] == "failed"
    assert "No active resume" in send_response.json()["last_error"]


def test_revive_failed_mail_jobs_requeues_recent_gmail_auth_failures(tmp_path):
    app, client = build_client(tmp_path)

    create_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "subject": "Hello",
                    "body_text": "Body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    job_id = create_response.json()["job_ids"][0]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'failed',
                failed_at = '2026-04-20T04:31:00+00:00',
                last_error = 'Gmail authorization expired. Reconnect Gmail. Gmail API request failed: {\"error\": \"invalid_grant\"}',
                updated_at = '2026-04-20T04:31:00+00:00'
            WHERE id = ?
            """,
            (job_id,),
        )

    connect_gmail(app)
    result = app.state.service.revive_failed_mail_jobs()
    job = client.get(f"/api/mail-jobs/{job_id}", headers=auth_headers()).json()

    assert result["ready"] is True
    assert result["revived_count"] == 1
    assert job["status"] == "pending"
    assert job["last_error"] is None


def test_revive_failed_mail_jobs_skips_old_failures(tmp_path):
    app, client = build_client(tmp_path)

    create_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "subject": "Hello",
                    "body_text": "Body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    job_id = create_response.json()["job_ids"][0]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'failed',
                created_at = '2026-04-10T04:31:00+00:00',
                failed_at = '2026-04-10T04:31:00+00:00',
                last_error = 'Gmail authorization expired. Reconnect Gmail. Gmail API request failed: {\"error\": \"invalid_grant\"}',
                updated_at = '2026-04-10T04:31:00+00:00'
            WHERE id = ?
            """,
            (job_id,),
        )

    connect_gmail(app)
    result = app.state.service.revive_failed_mail_jobs()
    job = client.get(f"/api/mail-jobs/{job_id}", headers=auth_headers()).json()

    assert result["ready"] is True
    assert result["revived_count"] == 0
    assert job["status"] == "failed"


def test_openoutreach_dashboard_shows_config_message_when_disabled(tmp_path):
    _, client = build_client(tmp_path)

    response = client.get("/openoutreach")

    assert response.status_code == 200
    assert "OpenOutreach DB is not configured" in response.text


def test_openoutreach_dashboard_renders_deals_and_detail(tmp_path):
    settings = Settings(
        api_bearer_token="test-token",
        database_path=str(tmp_path / "test.db"),
        files_dir=str(tmp_path / "files"),
        mail_provider="gmail_api",
        mail_from="user@example.com",
        gmail_client_id="client-id",
        gmail_client_secret="client-secret",
        gmail_redirect_uri="http://testserver/auth/gmail/callback",
        gmail_poll_seconds=60,
        gmail_watch_enabled=False,
        scheduler_poll_seconds=60,
        scheduler_enabled=False,
        openoutreach_database_url="postgres://readonly@example/openoutreach",
    )
    app = create_app(settings)
    client = TestClient(app)

    dashboard_payload = {
        "summary": {
            "total_deals": 1,
            "qualified_count": 1,
            "pending_count": 0,
            "connected_count": 0,
            "completed_count": 0,
            "failed_count": 0,
            "disqualified_count": 0,
            "message_enabled_count": 1,
            "has_embedding_count": 1,
            "has_summary_count": 1,
            "task_pending_count": 4,
            "task_running_count": 0,
            "task_failed_count": 0,
            "task_completed_count": 12,
            "state_counts": {"Qualified": 1},
        },
        "campaigns": [{"id": 1, "name": "Manual CRM"}],
        "deals": [
            {
                "deal_id": 77,
                "lead_id": 88,
                "campaign_id": 1,
                "campaign_name": "Manual CRM",
                "manual_mode": True,
                "public_identifier": "shristijoshi26",
                "linkedin_url": "https://www.linkedin.com/in/shristijoshi26/",
                "urn": "urn:li:fsd_profile:1",
                "disqualified": False,
                "lead_created_at": "2026-05-04T10:00:00+00:00",
                "lead_updated_at": "2026-05-04T10:10:00+00:00",
                "has_embedding": True,
                "embedding_bytes": 1536,
                "state": "Qualified",
                "outcome": "",
                "reason": "Waiting for connect pass",
                "connect_attempts": 0,
                "backoff_hours": 0,
                "deal_created_at": "2026-05-04T10:00:00+00:00",
                "deal_updated_at": "2026-05-04T10:10:00+00:00",
                "has_profile_summary": True,
                "has_chat_summary": False,
                "has_message": True,
                "message_enabled": True,
                "external_job_id": "job-123",
                "message_updated_at": "2026-05-04T10:11:00+00:00",
                "next_task_type": "follow_up",
                "next_task_status": "pending",
                "next_task_scheduled_at": "2026-05-04T12:00:00+00:00",
            }
        ],
    }
    detail_payload = {
        "deal_id": 77,
        "lead_id": 88,
        "campaign_id": 1,
        "campaign_name": "Manual CRM",
        "manual_mode": True,
        "public_identifier": "shristijoshi26",
        "linkedin_url": "https://www.linkedin.com/in/shristijoshi26/",
        "urn": "urn:li:fsd_profile:1",
        "disqualified": False,
        "lead_created_at": "2026-05-04T10:00:00+00:00",
        "lead_updated_at": "2026-05-04T10:10:00+00:00",
        "has_embedding": True,
        "embedding_bytes": 1536,
        "state": "Qualified",
        "outcome": "",
        "reason": "Waiting for connect pass",
        "connect_attempts": 0,
        "backoff_hours": 0,
        "deal_created_at": "2026-05-04T10:00:00+00:00",
        "deal_updated_at": "2026-05-04T10:10:00+00:00",
        "profile_summary": {"headline": "Backend Engineer"},
        "chat_summary": None,
        "has_message": True,
        "message_enabled": True,
        "message": "Hey there",
        "external_job_id": "job-123",
        "message_created_at": "2026-05-04T10:11:00+00:00",
        "message_updated_at": "2026-05-04T10:11:00+00:00",
        "tasks": [
            {
                "id": 5,
                "task_type": "follow_up",
                "status": "pending",
                "scheduled_at": "2026-05-04T12:00:00+00:00",
                "payload": {"public_id": "shristijoshi26"},
                "created_at": "2026-05-04T11:00:00+00:00",
                "started_at": None,
                "completed_at": None,
            }
        ],
        "recent_connect_tasks": [
            {
                "id": 4,
                "task_type": "connect",
                "status": "pending",
                "scheduled_at": "2026-05-04T11:30:00+00:00",
                "payload": {"campaign_id": 1},
                "created_at": "2026-05-04T11:00:00+00:00",
                "started_at": None,
                "completed_at": None,
            }
        ],
    }

    app.state.service.get_openoutreach_dashboard = lambda **kwargs: dashboard_payload
    app.state.service.get_openoutreach_deal = lambda deal_id: detail_payload

    response = client.get("/openoutreach?state=Qualified")
    assert response.status_code == 200
    assert "Enriched OpenOutreach deals" in response.text
    assert "shristijoshi26" in response.text
    assert "Waiting for connect pass" in response.text

    detail_response = client.get("/openoutreach/deals/77")
    assert detail_response.status_code == 200
    assert "Stored message" in detail_response.text
    assert "Backend Engineer" in detail_response.text


def test_send_now_refreshes_inbox_for_follow_up_and_blocks_if_reply_arrived(tmp_path):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    root_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Root email",
                    "body_text": "Initial body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    root_job_id = root_response.json()["job_ids"][0]

    follow_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Follow up",
                    "body_text": "Follow up body",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_id": root_job_id,
                    "reply_stop_enabled": True,
                }
            ]
        },
    )
    follow_up_id = follow_response.json()["job_ids"][0]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                message_id = '<mailtfoutofit.%s@example.com>',
                gmail_thread_id = 'thread-1'
            WHERE id = ?
            """
            % root_job_id,
            (root_job_id,),
        )

    sync_calls = {"count": 0}

    def fake_sync():
        sync_calls["count"] += 1
        with app.state.service.connect() as connection:
            contact_id = connection.execute(
                "SELECT contact_id FROM mail_jobs WHERE id = ?",
                (follow_up_id,),
            ).fetchone()["contact_id"]
            now = "2026-04-20T04:01:30+00:00"
            connection.execute(
                """
                UPDATE contacts
                SET has_replied = 1,
                    replied_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, contact_id),
            )
            connection.execute(
                """
                INSERT INTO inbound_messages (
                    id, contact_id, matched_job_id, gmail_message_id, gmail_thread_id, message_id,
                    in_reply_to, references_raw, from_email, subject, snippet, received_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "inbound-test-1",
                    contact_id,
                    root_job_id,
                    "gmail-inbound-1",
                    "thread-1",
                    "<inbound-test-1@example.com>",
                    "<mailtfoutofit.%s@example.com>" % root_job_id,
                    "<mailtfoutofit.%s@example.com>" % root_job_id,
                    "person@example.com",
                    "Re: Root email",
                    "Reply just arrived",
                    now,
                    now,
                ),
            )
        return {"connected": True, "processed_count": 1}

    delivered = {"count": 0}

    def fake_deliver(*args, **kwargs):
        delivered["count"] += 1
        raise AssertionError("follow-up should have been blocked before delivery")

    app.state.service.sync_inbound_replies = fake_sync
    app.state.service._deliver_job = fake_deliver

    send_response = client.post("/api/mail-jobs/%s/send-now" % follow_up_id, headers=auth_headers())
    assert send_response.status_code == 200
    assert send_response.json()["status"] == "blocked"
    assert send_response.json()["blocked_reason"] == "contact_replied"
    assert sync_calls["count"] == 1
    assert delivered["count"] == 0
    assert "already replied" in send_response.json()["attempts"][0]["error_message"]


def test_historical_reply_on_contact_does_not_block_new_root_thread(tmp_path):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    old_root_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Old root",
                    "body_text": "Old body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    old_root_id = old_root_response.json()["job_ids"][0]

    new_root_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "New root",
                    "body_text": "Fresh thread body",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    new_root_id = new_root_response.json()["job_ids"][0]

    with app.state.service.connect() as connection:
        contact_id = connection.execute(
            "SELECT contact_id FROM mail_jobs WHERE id = ?",
            (old_root_id,),
        ).fetchone()["contact_id"]
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                message_id = '<mailtfoutofit.%s@example.com>',
                gmail_thread_id = 'thread-old'
            WHERE id = ?
            """
            % old_root_id,
            (old_root_id,),
        )
        now = "2026-04-20T04:05:00+00:00"
        connection.execute(
            """
            UPDATE contacts
            SET has_replied = 1,
                replied_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (now, now, contact_id),
        )
        connection.execute(
            """
            INSERT INTO inbound_messages (
                id, contact_id, matched_job_id, gmail_message_id, gmail_thread_id, message_id,
                in_reply_to, references_raw, from_email, subject, snippet, received_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "inbound-old-root",
                contact_id,
                old_root_id,
                "gmail-inbound-old",
                "thread-old",
                "<inbound-old@example.com>",
                "<mailtfoutofit.%s@example.com>" % old_root_id,
                "<mailtfoutofit.%s@example.com>" % old_root_id,
                "person@example.com",
                "Re: Old root",
                "Old reply",
                now,
                now,
            ),
        )

    app.state.service.sync_inbound_replies = lambda: {"connected": True, "processed_count": 0}
    app.state.service._deliver_job = lambda job, attachment, auth_row, message_id, reply_context: {
        "message_id": message_id,
        "gmail_message_id": "gmail-message-id-new-root",
        "gmail_thread_id": "gmail-thread-id-new-root",
    }

    send_response = client.post("/api/mail-jobs/%s/send-now" % new_root_id, headers=auth_headers())
    assert send_response.status_code == 200
    assert send_response.json()["status"] == "sent"
    assert send_response.json()["gmail_thread_id"] == "gmail-thread-id-new-root"


def test_send_now_blocks_follow_up_from_live_gmail_thread_even_without_global_sync(tmp_path, monkeypatch):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    root_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Root email",
                    "body_text": "Initial body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    root_job_id = root_response.json()["job_ids"][0]

    follow_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Follow up",
                    "body_text": "Follow up body",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_id": root_job_id,
                    "reply_stop_enabled": True,
                }
            ]
        },
    )
    follow_up_id = follow_response.json()["job_ids"][0]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                message_id = '<mailtfoutofit.%s@example.com>',
                gmail_thread_id = 'thread-live-check'
            WHERE id = ?
            """
            % root_job_id,
            (root_job_id,),
        )

    app.state.service.sync_inbound_replies = lambda: {"connected": True, "processed_count": 0}
    monkeypatch.setattr(
        service_module.gmail_api,
        "get_thread",
        lambda access_token, gmail_thread_id: {
            "id": gmail_thread_id,
            "messages": [
                {
                    "id": "gmail-sent-root",
                    "threadId": gmail_thread_id,
                    "labelIds": ["SENT"],
                    "payload": {
                        "headers": [
                            {"name": "Message-ID", "value": "<mailtfoutofit.%s@example.com>" % root_job_id},
                            {"name": "From", "value": "User Example <user@example.com>"},
                            {"name": "Subject", "value": "Root email"},
                        ]
                    },
                },
                {
                    "id": "gmail-live-reply",
                    "threadId": gmail_thread_id,
                    "labelIds": ["INBOX"],
                    "snippet": "Reply already exists on the live thread",
                    "internalDate": "1776489694000",
                    "payload": {
                        "headers": [
                            {"name": "Message-ID", "value": "<live-thread-reply@example.com>"},
                            {"name": "In-Reply-To", "value": "<gmail-native-id@example.com>"},
                            {"name": "References", "value": "<gmail-native-id@example.com>"},
                            {"name": "From", "value": "Person Example <person@example.com>"},
                            {"name": "Subject", "value": "Re: Root email"},
                        ]
                    },
                },
            ],
        },
    )

    delivered = {"count": 0}

    def fake_deliver(*args, **kwargs):
        delivered["count"] += 1
        raise AssertionError("follow-up should have been blocked by live Gmail thread inspection")

    app.state.service._deliver_job = fake_deliver

    send_response = client.post("/api/mail-jobs/%s/send-now" % follow_up_id, headers=auth_headers())
    assert send_response.status_code == 200
    assert send_response.json()["status"] == "blocked"
    assert send_response.json()["blocked_reason"] == "contact_replied"
    assert delivered["count"] == 0


def test_patch_and_cancel_work_for_pending_jobs(tmp_path):
    _, client = build_client(tmp_path)
    create_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "subject": "Hello",
                    "body_text": "Body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    job_id = create_response.json()["job_ids"][0]

    patch_response = client.patch(
        "/api/mail-jobs/%s" % job_id,
        headers=auth_headers(),
        json={
            "subject": "Updated",
            "body_text": "Updated body",
            "scheduled_at": "2026-04-22T09:00:00+05:30",
        },
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["subject"] == "Updated"

    cancel_response = client.post("/api/mail-jobs/%s/cancel" % job_id, headers=auth_headers())
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"


def test_dashboard_shows_human_readable_requested_time(tmp_path):
    _, client = build_client(tmp_path)
    client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "batch_name": "human-time-check",
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "subject": "Hello",
                    "body_text": "Body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )

    response = client.get("/")
    assert response.status_code == 200
    assert "20 Apr 2026, 09:00 AM" in response.text
    assert "Server:" not in response.text
    assert "UTC" not in response.text


def test_queue_filters_support_contact_fields(tmp_path):
    _, client = build_client(tmp_path)
    client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "batch_name": "queue-filters",
            "jobs": [
                {
                    "recipient_email": "amazon@example.com",
                    "recipient_name": "Amazon Recruiter",
                    "company": "Amazon",
                    "title": "Recruiter",
                    "subject": "Hello Amazon",
                    "body_text": "Body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                },
                {
                    "recipient_email": "meta@example.com",
                    "recipient_name": "Meta Recruiter",
                    "company": "Meta",
                    "title": "Talent Partner",
                    "subject": "Hello Meta",
                    "body_text": "Body",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                },
            ],
        },
    )

    api_response = client.get("/api/mail-jobs?company=Amazon&name=Amazon&title=Recruiter", headers=auth_headers())
    assert api_response.status_code == 200
    jobs = api_response.json()["mail_jobs"]
    assert len(jobs) == 1
    assert jobs[0]["recipient_email"] == "amazon@example.com"

    dashboard_response = client.get("/?company=Amazon&name=Amazon&title=Recruiter")
    assert dashboard_response.status_code == 200
    assert "Company contains" in dashboard_response.text
    assert "Name contains" in dashboard_response.text
    assert "Title contains" in dashboard_response.text
    assert "amazon@example.com" in dashboard_response.text
    assert "meta@example.com" not in dashboard_response.text


def test_contacts_are_deduplicated_by_email_and_expose_history(tmp_path):
    app, client = build_client(tmp_path)
    connect_gmail(app)
    create_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "batch_name": "contacts-check",
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "company": "Amazon",
                    "title": "Recruiter",
                    "linkedin_url": "https://linkedin.com/in/person",
                    "subject": "First hello",
                    "body_text": "Body one",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                },
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "company": "Amazon",
                    "title": "Senior Recruiter",
                    "subject": "Second hello",
                    "body_text": "Body two",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                },
                {
                    "recipient_email": "other@example.com",
                    "recipient_name": "Other Person",
                    "subject": "No company",
                    "body_text": "Body three",
                    "scheduled_at": "2026-04-22T09:00:00+05:30",
                    "attach_latest_resume": False,
                },
            ],
        },
    )
    job_id = create_response.json()["job_ids"][0]

    app.state.service._deliver_job = lambda job, attachment, auth_row, message_id, reply_context: {
        "message_id": message_id,
        "gmail_message_id": "gmail-message-id",
        "gmail_thread_id": "gmail-thread-id",
    }
    send_response = client.post("/api/mail-jobs/%s/send-now" % job_id, headers=auth_headers())
    assert send_response.status_code == 200

    contacts_response = client.get("/api/contacts", headers=auth_headers())
    assert contacts_response.status_code == 200
    contacts = contacts_response.json()["contacts"]
    assert len(contacts) == 2

    amazon_response = client.get("/api/contacts?company=Amazon", headers=auth_headers())
    amazon_contacts = amazon_response.json()["contacts"]
    assert len(amazon_contacts) == 1
    assert amazon_contacts[0]["email"] == "person@example.com"
    assert amazon_contacts[0]["sent_count"] == 1
    assert amazon_contacts[0]["title"] == "Senior Recruiter"

    missing_company_response = client.get("/api/contacts?company_mode=missing", headers=auth_headers())
    missing_company_contacts = missing_company_response.json()["contacts"]
    assert len(missing_company_contacts) == 1
    assert missing_company_contacts[0]["email"] == "other@example.com"

    linkedin_response = client.get("/api/contacts?has_linkedin=true", headers=auth_headers())
    assert [item["email"] for item in linkedin_response.json()["contacts"]] == ["person@example.com"]

    sent_mail_response = client.get("/api/contacts?has_sent_mail=true", headers=auth_headers())
    assert [item["email"] for item in sent_mail_response.json()["contacts"]] == ["person@example.com"]

    contact_id = amazon_contacts[0]["id"]
    detail_response = client.get("/api/contacts/%s" % contact_id, headers=auth_headers())
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert len(detail["mail_jobs"]) == 2
    assert detail["mail_jobs"][0]["subject"] == "Second hello"

    contacts_page = client.get("/contacts")
    assert contacts_page.status_code == 200
    assert "Person Example" in contacts_page.text
    assert "Amazon" in contacts_page.text

    detail_page = client.get("/contacts/%s" % contact_id)
    assert detail_page.status_code == 200
    assert "Mail threads" in detail_page.text
    assert "Second hello" in detail_page.text


def test_existing_contacts_schema_is_migrated_in_place(tmp_path):
    database_path = tmp_path / "test.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE contacts (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT,
            company TEXT,
            title TEXT,
            linkedin_url TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO contacts (id, email, name, company, title, linkedin_url, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "contact-1",
            "legacy@example.com",
            "Legacy Contact",
            "Amazon",
            "Recruiter",
            "https://linkedin.com/in/legacy",
            "2026-04-18T00:00:00+00:00",
            "2026-04-18T00:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()

    settings = Settings(
        api_bearer_token="test-token",
        database_path=str(database_path),
        files_dir=str(tmp_path / "files"),
        mail_provider="gmail_api",
        mail_from="user@example.com",
        gmail_client_id="client-id",
        gmail_client_secret="client-secret",
        gmail_redirect_uri="http://testserver/auth/gmail/callback",
        gmail_poll_seconds=60,
        gmail_watch_enabled=False,
        scheduler_poll_seconds=60,
        scheduler_enabled=False,
    )
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/contacts", headers=auth_headers())
    assert response.status_code == 200
    contacts = response.json()["contacts"]
    assert len(contacts) == 1
    assert contacts[0]["email"] == "legacy@example.com"
    assert contacts[0]["source"] == "local_ai"
    assert contacts[0]["apollo_person_id"] is None


def test_mail_send_attempts_fk_is_rebound_after_mail_job_migration(tmp_path):
    database_path = tmp_path / "test.db"
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.executescript(
        """
        CREATE TABLE contacts (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT,
            company TEXT,
            title TEXT,
            linkedin_url TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE mail_jobs (
            id TEXT PRIMARY KEY,
            contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            batch_name TEXT,
            source TEXT NOT NULL DEFAULT 'local_ai',
            subject TEXT NOT NULL,
            body_text TEXT NOT NULL,
            body_html TEXT,
            scheduled_at TEXT NOT NULL,
            requested_scheduled_at TEXT NOT NULL,
            timezone_label TEXT,
            status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'cancelled')),
            attachment_mode TEXT NOT NULL CHECK (attachment_mode IN ('none', 'latest_resume')),
            sent_at TEXT,
            failed_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE mail_send_attempts (
            id TEXT PRIMARY KEY,
            mail_job_id TEXT NOT NULL REFERENCES mail_jobs(id) ON DELETE CASCADE,
            attempted_at TEXT NOT NULL,
            success INTEGER NOT NULL,
            error_message TEXT,
            smtp_message_id TEXT
        );
        """
    )
    connection.execute("ALTER TABLE mail_jobs RENAME TO mail_jobs_old")
    connection.commit()
    connection.close()

    settings = Settings(
        api_bearer_token="test-token",
        database_path=str(database_path),
        files_dir=str(tmp_path / "files"),
        mail_provider="gmail_api",
        mail_from="user@example.com",
        gmail_client_id="client-id",
        gmail_client_secret="client-secret",
        gmail_redirect_uri="http://testserver/auth/gmail/callback",
        gmail_poll_seconds=60,
        gmail_watch_enabled=False,
        scheduler_poll_seconds=60,
        scheduler_enabled=False,
    )
    app = create_app(settings)

    with app.state.service.connect() as migrated:
        fk_rows = migrated.execute("PRAGMA foreign_key_list(mail_send_attempts)").fetchall()
    assert fk_rows[0]["table"] == "mail_jobs"


def test_inbound_messages_fk_is_rebound_after_mail_job_migration(tmp_path):
    database_path = tmp_path / "test.db"
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.executescript(
        """
        CREATE TABLE contacts (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT,
            company TEXT,
            title TEXT,
            linkedin_url TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE mail_jobs (
            id TEXT PRIMARY KEY,
            contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            batch_name TEXT,
            source TEXT NOT NULL DEFAULT 'local_ai',
            subject TEXT NOT NULL,
            body_text TEXT NOT NULL,
            body_html TEXT,
            scheduled_at TEXT NOT NULL,
            requested_scheduled_at TEXT NOT NULL,
            timezone_label TEXT,
            status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'cancelled')),
            attachment_mode TEXT NOT NULL CHECK (attachment_mode IN ('none', 'latest_resume')),
            sent_at TEXT,
            failed_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

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
        );
        """
    )
    connection.execute("ALTER TABLE mail_jobs RENAME TO mail_jobs_old")
    connection.commit()
    connection.close()

    settings = Settings(
        api_bearer_token="test-token",
        database_path=str(database_path),
        files_dir=str(tmp_path / "files"),
        mail_provider="gmail_api",
        mail_from="user@example.com",
        gmail_client_id="client-id",
        gmail_client_secret="client-secret",
        gmail_redirect_uri="http://testserver/auth/gmail/callback",
        gmail_poll_seconds=60,
        gmail_watch_enabled=False,
        scheduler_poll_seconds=60,
        scheduler_enabled=False,
    )
    app = create_app(settings)

    with app.state.service.connect() as migrated:
        fk_rows = migrated.execute("PRAGMA foreign_key_list(inbound_messages)").fetchall()
    assert sorted(row["table"] for row in fk_rows) == ["contacts", "mail_jobs"]


def test_record_attempt_repairs_mail_send_attempts_fk_if_old_mail_jobs_table_leaks(tmp_path):
    database_path = tmp_path / "test.db"
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.executescript(
        """
        CREATE TABLE contacts (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT,
            company TEXT,
            title TEXT,
            linkedin_url TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE mail_jobs (
            id TEXT PRIMARY KEY,
            contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
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
            blocked_reason TEXT,
            sent_at TEXT,
            failed_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE mail_send_attempts (
            id TEXT PRIMARY KEY,
            mail_job_id TEXT NOT NULL REFERENCES mail_jobs_old(id) ON DELETE CASCADE,
            attempted_at TEXT NOT NULL,
            success INTEGER NOT NULL,
            error_message TEXT,
            smtp_message_id TEXT
        );
        """
    )
    connection.execute(
        """
        INSERT INTO contacts (id, email, created_at, updated_at)
        VALUES ('contact-1', 'person@example.com', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        """
    )
    connection.execute(
        """
        INSERT INTO mail_jobs (
            id, contact_id, subject, body_text, scheduled_at, requested_scheduled_at,
            status, attachment_mode, created_at, updated_at
        )
        VALUES (
            'job-1', 'contact-1', 'Hello', 'Body',
            '2026-04-20T09:00:00+00:00', '2026-04-20T09:00:00+00:00',
            'pending', 'none', '2026-04-19T00:00:00+00:00', '2026-04-19T00:00:00+00:00'
        )
        """
    )
    connection.commit()
    connection.close()

    settings = Settings(
        api_bearer_token="test-token",
        database_path=str(database_path),
        files_dir=str(tmp_path / "files"),
        mail_provider="gmail_api",
        mail_from="user@example.com",
        gmail_client_id="client-id",
        gmail_client_secret="client-secret",
        gmail_redirect_uri="http://testserver/auth/gmail/callback",
        gmail_poll_seconds=60,
        gmail_watch_enabled=False,
        scheduler_poll_seconds=60,
        scheduler_enabled=False,
    )
    service = service_module.MailSchedulerService(settings)

    with service.connect() as migrated:
        service._record_attempt(migrated, "job-1", True, None, "gmail-msg-1")
        fk_rows = migrated.execute("PRAGMA foreign_key_list(mail_send_attempts)").fetchall()
        attempts = migrated.execute("SELECT * FROM mail_send_attempts").fetchall()

    assert fk_rows[0]["table"] == "mail_jobs"
    assert len(attempts) == 1


def test_gmail_oauth_status_and_callback(tmp_path, monkeypatch):
    app, client = build_client(tmp_path)

    response = client.get("/auth/gmail/status")
    assert response.status_code == 200
    assert response.json()["connected"] is False

    monkeypatch.setattr(
        service_module.gmail_api,
        "exchange_code_for_tokens",
        lambda client_id, client_secret, redirect_uri, code: {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "scope": "scope-a scope-b",
            "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        service_module.gmail_api,
        "get_profile",
        lambda access_token: {"emailAddress": "oauth-user@gmail.com"},
    )

    app.state.gmail_oauth_states["state-1"] = "http://testserver/auth/gmail/callback"
    callback = client.get("/auth/gmail/callback?state=state-1&code=auth-code", follow_redirects=False)
    assert callback.status_code == 303

    response = client.get("/auth/gmail/status")
    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert response.json()["email"] == "oauth-user@gmail.com"


def test_follow_up_headers_and_root_chain(tmp_path):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    create_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "batch_name": "threading-check",
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "subject": "First touch",
                    "body_text": "Initial body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ],
        },
    )
    root_job_id = create_response.json()["job_ids"][0]

    create_followup = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "subject": "Follow up",
                    "body_text": "Second body",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_id": root_job_id,
                }
            ]
        },
    )
    follow_up_id = create_followup.json()["job_ids"][0]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                message_id = '<mailtfoutofit.%s@example.com>',
                gmail_thread_id = 'thread-1'
            WHERE id = ?
            """
            % root_job_id,
            (root_job_id,),
        )
        follow_up = app.state.service._load_job_row(connection, follow_up_id)
        reply_context = app.state.service._resolve_reply_context(connection, follow_up)
        message = app.state.service._build_email_message(
            follow_up,
            attachment=None,
            sender_email="user@example.com",
            message_id="<mailtfoutofit.%s@example.com>" % follow_up_id,
            reply_context=reply_context,
        )

    assert reply_context["thread_id"] == "thread-1"
    assert reply_context["parent_message_id"] == "<mailtfoutofit.%s@example.com>" % root_job_id
    assert message["In-Reply-To"] == "<mailtfoutofit.%s@example.com>" % root_job_id
    assert message["References"] == "<mailtfoutofit.%s@example.com>" % root_job_id

    detail = client.get("/api/mail-jobs/%s" % follow_up_id, headers=auth_headers()).json()
    assert detail["parent_job_id"] == root_job_id
    assert detail["root_job_id"] == root_job_id


def test_bulk_request_can_create_three_email_thread_with_client_aliases(tmp_path):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "batch_name": "single-call-thread",
            "jobs": [
                {
                    "client_job_id": "root",
                    "recipient_email": "person@example.com",
                    "subject": "First touch",
                    "body_text": "Initial body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                },
                {
                    "client_job_id": "second",
                    "recipient_email": "person@example.com",
                    "subject": "Follow up one",
                    "body_text": "Second body",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_client_id": "root",
                },
                {
                    "client_job_id": "third",
                    "recipient_email": "person@example.com",
                    "subject": "Follow up two",
                    "body_text": "Third body",
                    "scheduled_at": "2026-04-22T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_client_id": "second",
                },
            ],
        },
    )
    assert response.status_code == 200

    root_job_id, second_job_id, third_job_id = response.json()["job_ids"]
    root_job = client.get("/api/mail-jobs/%s" % root_job_id, headers=auth_headers()).json()
    second_job = client.get("/api/mail-jobs/%s" % second_job_id, headers=auth_headers()).json()
    third_job = client.get("/api/mail-jobs/%s" % third_job_id, headers=auth_headers()).json()

    assert root_job["parent_job_id"] is None
    assert root_job["root_job_id"] == root_job_id
    assert second_job["parent_job_id"] == root_job_id
    assert second_job["root_job_id"] == root_job_id
    assert third_job["parent_job_id"] == second_job_id
    assert third_job["root_job_id"] == root_job_id

    root_message_id = "<mailtfoutofit.%s@example.com>" % root_job_id
    second_message_id = "<mailtfoutofit.%s@example.com>" % second_job_id
    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                message_id = ?,
                gmail_thread_id = 'thread-alias'
            WHERE id = ?
            """,
            (root_message_id, root_job_id),
        )
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-21T04:00:00+00:00',
                message_id = ?,
                gmail_thread_id = 'thread-alias'
            WHERE id = ?
            """,
            (second_message_id, second_job_id),
        )
        third_job_row = app.state.service._load_job_row(connection, third_job_id)
        reply_context = app.state.service._resolve_reply_context(connection, third_job_row)

    assert reply_context["thread_id"] == "thread-alias"
    assert reply_context["parent_message_id"] == second_message_id
    assert reply_context["references"] == [root_message_id, second_message_id]


def test_bulk_request_rejects_unknown_parent_job_client_id(tmp_path):
    _, client = build_client(tmp_path)

    response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "client_job_id": "child",
                    "recipient_email": "person@example.com",
                    "subject": "Follow up",
                    "body_text": "Body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_client_id": "missing-root",
                }
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "parent_job_client_id does not exist in this request"


def test_three_email_thread_uses_latest_sent_ancestor_and_full_references(tmp_path):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    root_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "batch_name": "three-thread-check",
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "subject": "First touch",
                    "body_text": "Initial body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ],
        },
    )
    root_job_id = root_response.json()["job_ids"][0]

    second_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "subject": "Follow up one",
                    "body_text": "Second body",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_id": root_job_id,
                }
            ]
        },
    )
    second_job_id = second_response.json()["job_ids"][0]

    third_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "subject": "Follow up two",
                    "body_text": "Third body",
                    "scheduled_at": "2026-04-22T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_id": second_job_id,
                }
            ]
        },
    )
    third_job_id = third_response.json()["job_ids"][0]

    root_message_id = "<mailtfoutofit.%s@example.com>" % root_job_id
    second_message_id = "<mailtfoutofit.%s@example.com>" % second_job_id

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                message_id = ?,
                gmail_thread_id = 'thread-1'
            WHERE id = ?
            """,
            (root_message_id, root_job_id),
        )
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-21T04:00:00+00:00',
                message_id = ?,
                gmail_thread_id = 'thread-1'
            WHERE id = ?
            """,
            (second_message_id, second_job_id),
        )
        third_job = app.state.service._load_job_row(connection, third_job_id)
        reply_context = app.state.service._resolve_reply_context(connection, third_job)
        message = app.state.service._build_email_message(
            third_job,
            attachment=None,
            sender_email="user@example.com",
            message_id="<mailtfoutofit.%s@example.com>" % third_job_id,
            reply_context=reply_context,
        )

    assert reply_context["thread_id"] == "thread-1"
    assert reply_context["parent_message_id"] == second_message_id
    assert reply_context["references"] == [root_message_id, second_message_id]
    assert message["In-Reply-To"] == second_message_id
    assert message["References"] == "%s %s" % (root_message_id, second_message_id)

    third_detail = client.get("/api/mail-jobs/%s" % third_job_id, headers=auth_headers()).json()
    assert third_detail["parent_job_id"] == second_job_id
    assert third_detail["root_job_id"] == root_job_id


def test_reply_sync_blocks_pending_follow_ups(tmp_path, monkeypatch):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    root_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Root email",
                    "body_text": "Initial body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    root_job_id = root_response.json()["job_ids"][0]

    follow_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Follow up",
                    "body_text": "Follow up body",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_id": root_job_id,
                }
            ]
        },
    )
    follow_up_id = follow_response.json()["job_ids"][0]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                message_id = '<mailtfoutofit.%s@example.com>',
                gmail_thread_id = 'thread-1'
            WHERE id = ?
            """
            % root_job_id,
            (root_job_id,),
        )

    monkeypatch.setattr(
        service_module.gmail_api,
        "list_recent_messages",
        lambda access_token, max_results=50: [{"id": "inbound-1"}],
    )
    monkeypatch.setattr(
        service_module.gmail_api,
        "get_message_metadata",
        lambda access_token, gmail_message_id: {
            "id": gmail_message_id,
            "threadId": "thread-1",
            "historyId": "200",
            "labelIds": ["INBOX"],
            "snippet": "Thanks for reaching out",
            "internalDate": "1776489600000",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<inbound-1@example.com>"},
                    {"name": "In-Reply-To", "value": "<mailtfoutofit.%s@example.com>" % root_job_id},
                    {"name": "References", "value": "<mailtfoutofit.%s@example.com>" % root_job_id},
                    {"name": "From", "value": "Person Example <person@example.com>"},
                    {"name": "Subject", "value": "Re: Root email"},
                ]
            },
        },
    )

    sync_result = app.state.service.sync_inbound_replies()
    assert sync_result["processed_count"] == 1

    follow_up = client.get("/api/mail-jobs/%s" % follow_up_id, headers=auth_headers()).json()
    assert follow_up["status"] == "blocked"
    assert follow_up["blocked_reason"] == "contact_replied"

    with app.state.service.connect() as connection:
        root_contact_id = connection.execute("SELECT contact_id FROM mail_jobs WHERE id = ?", (root_job_id,)).fetchone()["contact_id"]
    contact = client.get("/api/contacts/%s" % root_contact_id, headers=auth_headers()).json()
    assert contact["has_replied"] is True
    assert len(contact["inbound_messages"]) == 1


def test_reply_sync_falls_back_to_gmail_thread_id_when_headers_do_not_match_custom_message_id(tmp_path, monkeypatch):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    root_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Root email",
                    "body_text": "Initial body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    root_job_id = root_response.json()["job_ids"][0]

    follow_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Follow up",
                    "body_text": "Follow up body",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_id": root_job_id,
                }
            ]
        },
    )
    follow_up_id = follow_response.json()["job_ids"][0]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                message_id = '<mailtfoutofit.%s@example.com>',
                gmail_thread_id = 'gmail-thread-1'
            WHERE id = ?
            """
            % root_job_id,
            (root_job_id,),
        )

    monkeypatch.setattr(
        service_module.gmail_api,
        "list_recent_messages",
        lambda access_token, max_results=50: [{"id": "inbound-thread-fallback"}],
    )
    monkeypatch.setattr(
        service_module.gmail_api,
        "get_message_metadata",
        lambda access_token, gmail_message_id: {
            "id": gmail_message_id,
            "threadId": "gmail-thread-1",
            "historyId": "210",
            "labelIds": ["INBOX"],
            "snippet": "Reply came back with Gmail-native references only",
            "internalDate": "1776489600000",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<inbound-thread-fallback@example.com>"},
                    {"name": "In-Reply-To", "value": "<gmail-native-id@example.com>"},
                    {"name": "References", "value": "<gmail-native-id@example.com>"},
                    {"name": "From", "value": "Person Example <person@example.com>"},
                    {"name": "Subject", "value": "Re: Root email"},
                ]
            },
        },
    )

    sync_result = app.state.service.sync_inbound_replies()
    assert sync_result["processed_count"] == 1

    follow_up = client.get("/api/mail-jobs/%s" % follow_up_id, headers=auth_headers()).json()
    assert follow_up["status"] == "blocked"
    assert follow_up["blocked_reason"] == "contact_replied"


def test_reply_sync_blocks_all_pending_descendants_in_same_thread(tmp_path, monkeypatch):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    root_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Root email",
                    "body_text": "Initial body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    root_job_id = root_response.json()["job_ids"][0]

    second_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Follow up one",
                    "body_text": "Second body",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_id": root_job_id,
                }
            ]
        },
    )
    second_job_id = second_response.json()["job_ids"][0]

    third_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Follow up two",
                    "body_text": "Third body",
                    "scheduled_at": "2026-04-22T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_id": second_job_id,
                }
            ]
        },
    )
    third_job_id = third_response.json()["job_ids"][0]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                message_id = '<mailtfoutofit.%s@example.com>',
                gmail_thread_id = 'thread-desc'
            WHERE id = ?
            """
            % root_job_id,
            (root_job_id,),
        )

    monkeypatch.setattr(
        service_module.gmail_api,
        "list_recent_messages",
        lambda access_token, max_results=50: [{"id": "inbound-desc"}],
    )
    monkeypatch.setattr(
        service_module.gmail_api,
        "get_message_metadata",
        lambda access_token, gmail_message_id: {
            "id": gmail_message_id,
            "threadId": "thread-desc",
            "historyId": "220",
            "labelIds": ["INBOX"],
            "snippet": "Thanks for reaching out",
            "internalDate": "1776489600000",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<inbound-desc@example.com>"},
                    {"name": "In-Reply-To", "value": "<mailtfoutofit.%s@example.com>" % root_job_id},
                    {"name": "References", "value": "<mailtfoutofit.%s@example.com>" % root_job_id},
                    {"name": "From", "value": "Person Example <person@example.com>"},
                    {"name": "Subject", "value": "Re: Root email"},
                ]
            },
        },
    )

    sync_result = app.state.service.sync_inbound_replies()
    assert sync_result["processed_count"] == 1

    second_detail = client.get("/api/mail-jobs/%s" % second_job_id, headers=auth_headers()).json()
    third_detail = client.get("/api/mail-jobs/%s" % third_job_id, headers=auth_headers()).json()

    assert second_detail["status"] == "blocked"
    assert second_detail["blocked_reason"] == "contact_replied"
    assert third_detail["status"] == "blocked"
    assert third_detail["blocked_reason"] == "contact_replied"


def test_reply_sync_handles_recent_messages_payload_shape(tmp_path, monkeypatch):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    root_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Root email",
                    "body_text": "Initial body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    root_job_id = root_response.json()["job_ids"][0]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                message_id = '<mailtfoutofit.%s@example.com>',
                gmail_thread_id = 'thread-recent'
            WHERE id = ?
            """
            % root_job_id,
            (root_job_id,),
        )
        connection.execute("UPDATE gmail_auth_state SET history_id = NULL WHERE id = 1")

    monkeypatch.setattr(
        service_module.gmail_api,
        "list_recent_messages",
        lambda access_token, max_results=50: {"messages": [{"id": "recent-1"}], "resultSizeEstimate": 1},
    )
    monkeypatch.setattr(
        service_module.gmail_api,
        "get_message_metadata",
        lambda access_token, gmail_message_id: {
            "id": gmail_message_id,
            "threadId": "thread-recent",
            "historyId": "300",
            "labelIds": ["INBOX"],
            "snippet": "Reply from recent messages branch",
            "internalDate": "1776489600000",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<recent-1@example.com>"},
                    {"name": "In-Reply-To", "value": "<mailtfoutofit.%s@example.com>" % root_job_id},
                    {"name": "References", "value": "<mailtfoutofit.%s@example.com>" % root_job_id},
                    {"name": "From", "value": "Person Example <person@example.com>"},
                    {"name": "Subject", "value": "Re: Root email"},
                ]
            },
        },
    )

    sync_result = app.state.service.sync_inbound_replies()
    assert sync_result["processed_count"] == 1
    assert sync_result["history_id"] == "300"


def test_reply_sync_rescans_recent_inbox_even_when_history_checkpoint_exists(tmp_path, monkeypatch):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    root_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Root email",
                    "body_text": "Initial body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    root_job_id = root_response.json()["job_ids"][0]

    follow_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Follow up",
                    "body_text": "Follow up body",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_id": root_job_id,
                }
            ]
        },
    )
    follow_up_id = follow_response.json()["job_ids"][0]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                message_id = '<mailtfoutofit.%s@example.com>',
                gmail_thread_id = 'gmail-thread-rescan'
            WHERE id = ?
            """
            % root_job_id,
            (root_job_id,),
        )
        connection.execute("UPDATE gmail_auth_state SET history_id = '999'")

    monkeypatch.setattr(
        service_module.gmail_api,
        "list_history",
        lambda access_token, start_history_id: {"history": [], "historyId": start_history_id},
    )
    monkeypatch.setattr(
        service_module.gmail_api,
        "list_recent_messages",
        lambda access_token, max_results=25: {"messages": [{"id": "recent-rescan-1"}], "resultSizeEstimate": 1},
    )
    monkeypatch.setattr(
        service_module.gmail_api,
        "get_message_metadata",
        lambda access_token, gmail_message_id: {
            "id": gmail_message_id,
            "threadId": "gmail-thread-rescan",
            "historyId": "1000",
            "labelIds": ["INBOX"],
            "snippet": "Reply rescued by recent rescan",
            "internalDate": "1776489600000",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<recent-rescan-1@example.com>"},
                    {"name": "In-Reply-To", "value": "<gmail-native-id@example.com>"},
                    {"name": "References", "value": "<gmail-native-id@example.com>"},
                    {"name": "From", "value": "Person Example <person@example.com>"},
                    {"name": "Subject", "value": "Re: Root email"},
                ]
            },
        },
    )

    sync_result = app.state.service.sync_inbound_replies()
    assert sync_result["processed_count"] == 1
    assert sync_result["history_id"] == "1000"

    follow_up = client.get("/api/mail-jobs/%s" % follow_up_id, headers=auth_headers()).json()
    assert follow_up["status"] == "blocked"


def test_reply_sync_falls_back_to_recent_scan_when_history_checkpoint_returns_not_found(tmp_path, monkeypatch):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    root_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Root email",
                    "body_text": "Initial body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    root_job_id = root_response.json()["job_ids"][0]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                message_id = '<mailtfoutofit.%s@example.com>',
                gmail_thread_id = 'thread-history-fallback'
            WHERE id = ?
            """
            % root_job_id,
            (root_job_id,),
        )
        connection.execute("UPDATE gmail_auth_state SET history_id = 'stale-history-id'")

    monkeypatch.setattr(
        service_module.gmail_api,
        "list_history",
        lambda access_token, start_history_id: (_ for _ in ()).throw(
            service_module.gmail_api.GmailApiError(
                'Gmail API request failed: {"error":{"code":404,"status":"NOT_FOUND","reason":"notFound"}}'
            )
        ),
    )
    monkeypatch.setattr(
        service_module.gmail_api,
        "list_recent_messages",
        lambda access_token, max_results=50: {"messages": [{"id": "history-fallback-recent"}], "resultSizeEstimate": 1},
    )
    monkeypatch.setattr(
        service_module.gmail_api,
        "get_message_metadata",
        lambda access_token, gmail_message_id: {
            "id": gmail_message_id,
            "threadId": "thread-history-fallback",
            "historyId": "501",
            "labelIds": ["INBOX"],
            "snippet": "Reply recovered after stale history checkpoint",
            "internalDate": "1776489600000",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<history-fallback@example.com>"},
                    {"name": "In-Reply-To", "value": "<mailtfoutofit.%s@example.com>" % root_job_id},
                    {"name": "References", "value": "<mailtfoutofit.%s@example.com>" % root_job_id},
                    {"name": "From", "value": "Person Example <person@example.com>"},
                    {"name": "Subject", "value": "Re: Root email"},
                ]
            },
        },
    )

    sync_result = app.state.service.sync_inbound_replies()
    assert sync_result["processed_count"] == 1
    assert sync_result["history_id"] == "501"

    status = client.get("/auth/gmail/status").json()
    assert status["last_error"] is None


def test_reply_sync_skips_not_found_message_metadata_and_clears_last_error_on_success(tmp_path, monkeypatch):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    with app.state.service.connect() as connection:
        app.state.service._store_gmail_auth(
            connection,
            email="user@example.com",
            access_token="access-token",
            refresh_token="refresh-token",
            expiry="2099-01-01T00:00:00+00:00",
            scope="gmail.send gmail.readonly",
            history_id=None,
            last_inbox_sync_at="2026-04-19T00:00:00+00:00",
            last_error="stale-error",
        )

    monkeypatch.setattr(
        service_module.gmail_api,
        "list_recent_messages",
        lambda access_token, max_results=50: {"messages": [{"id": "missing-1"}, {"id": "ok-2"}], "resultSizeEstimate": 2},
    )

    def fake_metadata(access_token, gmail_message_id):
        if gmail_message_id == "missing-1":
            raise service_module.gmail_api.GmailApiError(
                'Gmail API request failed: {"error":{"code":404,"status":"NOT_FOUND","reason":"notFound"}}'
            )
        return {
            "id": gmail_message_id,
            "threadId": "thread-ok-2",
            "historyId": "777",
            "labelIds": ["INBOX"],
            "snippet": "Non-matching inbox mail",
            "internalDate": "1776489600000",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<ok-2@example.com>"},
                    {"name": "From", "value": "Other Person <other@example.com>"},
                    {"name": "Subject", "value": "Hello"},
                ]
            },
        }

    monkeypatch.setattr(service_module.gmail_api, "get_message_metadata", fake_metadata)

    sync_result = app.state.service.sync_inbound_replies()
    assert sync_result["processed_count"] == 0
    assert sync_result["history_id"] == "777"

    status = client.get("/auth/gmail/status").json()
    assert status["last_error"] is None


def test_reply_sync_can_upgrade_previously_unmatched_inbound_message(tmp_path, monkeypatch):
    app, client = build_client(tmp_path)
    connect_gmail(app)

    root_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Root email",
                    "body_text": "Initial body",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                }
            ]
        },
    )
    root_job_id = root_response.json()["job_ids"][0]

    follow_response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth_headers(),
        json={
            "jobs": [
                {
                    "recipient_email": "person@example.com",
                    "recipient_name": "Person Example",
                    "subject": "Follow up",
                    "body_text": "Follow up body",
                    "scheduled_at": "2026-04-21T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_id": root_job_id,
                }
            ]
        },
    )
    follow_up_id = follow_response.json()["job_ids"][0]

    with app.state.service.connect() as connection:
        contact_id = connection.execute(
            "SELECT contact_id FROM mail_jobs WHERE id = ?",
            (root_job_id,),
        ).fetchone()["contact_id"]
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                message_id = '<mailtfoutofit.%s@example.com>',
                gmail_thread_id = 'gmail-thread-upgrade'
            WHERE id = ?
            """
            % root_job_id,
            (root_job_id,),
        )
        connection.execute(
            """
            INSERT INTO inbound_messages (
                id, contact_id, matched_job_id, gmail_message_id, gmail_thread_id, message_id,
                in_reply_to, references_raw, from_email, subject, snippet, received_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "existing-unmatched-reply",
                None,
                None,
                "gmail-upgrade-1",
                "gmail-thread-upgrade",
                "<upgrade-reply@example.com>",
                "<gmail-native-id@example.com>",
                "<gmail-native-id@example.com>",
                "person@example.com",
                "Re: Root email",
                "Unmatched reply stored earlier",
                "2026-04-20T04:01:00+00:00",
                "2026-04-20T04:01:00+00:00",
            ),
        )
        connection.execute("UPDATE gmail_auth_state SET history_id = '999'")

    monkeypatch.setattr(
        service_module.gmail_api,
        "list_history",
        lambda access_token, start_history_id: {"history": [], "historyId": start_history_id},
    )
    monkeypatch.setattr(
        service_module.gmail_api,
        "list_recent_messages",
        lambda access_token, max_results=25: {"messages": [{"id": "gmail-upgrade-1"}], "resultSizeEstimate": 1},
    )
    monkeypatch.setattr(
        service_module.gmail_api,
        "get_message_metadata",
        lambda access_token, gmail_message_id: {
            "id": gmail_message_id,
            "threadId": "gmail-thread-upgrade",
            "historyId": "1001",
            "labelIds": ["INBOX"],
            "snippet": "Previously unmatched reply is now recoverable",
            "internalDate": "1776489660000",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<upgrade-reply@example.com>"},
                    {"name": "In-Reply-To", "value": "<gmail-native-id@example.com>"},
                    {"name": "References", "value": "<gmail-native-id@example.com>"},
                    {"name": "From", "value": "Person Example <person@example.com>"},
                    {"name": "Subject", "value": "Re: Root email"},
                ]
            },
        },
    )

    sync_result = app.state.service.sync_inbound_replies()
    assert sync_result["processed_count"] == 1

    follow_up = client.get("/api/mail-jobs/%s" % follow_up_id, headers=auth_headers()).json()
    assert follow_up["status"] == "blocked"

    with app.state.service.connect() as connection:
        upgraded = connection.execute(
            "SELECT contact_id, matched_job_id FROM inbound_messages WHERE gmail_message_id = ?",
            ("gmail-upgrade-1",),
        ).fetchone()
    assert upgraded["contact_id"] == contact_id
    assert upgraded["matched_job_id"] == root_job_id

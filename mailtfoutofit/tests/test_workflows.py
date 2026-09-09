from fastapi.testclient import TestClient

from mail_scheduler.app import create_app
from mail_scheduler.config import Settings


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
        openoutreach_base_url="https://openoutreach.example.com",
        openoutreach_api_token="ootoken",
        openoutreach_campaign_id="1",
    )
    app = create_app(settings)
    return app, TestClient(app)


def auth_headers():
    return {"Authorization": "Bearer test-token"}


def test_workflow_ingestion_creates_mail_and_linkedin_jobs(tmp_path):
    _, client = build_client(tmp_path)

    response = client.post(
        "/api/outreach-workflows/bulk",
        headers=auth_headers(),
        json={
            "batch_name": "wf-1",
            "workflows": [
                {
                    "client_workflow_id": "contact-1",
                    "contact": {
                        "recipient_email": "person@example.com",
                        "recipient_name": "Person Example",
                        "company": "Acme",
                        "title": "Founder",
                        "linkedin_url": "https://www.linkedin.com/in/person/",
                    },
                    "steps": [
                        {
                            "type": "email",
                            "step_id": "email-1",
                            "subject": "Hello",
                            "body_text": "Body",
                            "scheduled_at": "2026-04-26T09:00:00+05:30",
                        },
                        {
                            "type": "linkedin",
                            "kind": "connection_request",
                            "step_id": "li-connect",
                            "scheduled_at": "2026-04-26T12:00:00+05:30",
                        },
                        {
                            "type": "email",
                            "step_id": "email-2",
                            "thread_parent_step_id": "email-1",
                            "subject": "Follow up",
                            "body_text": "Second body",
                            "scheduled_at": "2026-04-28T09:00:00+05:30",
                        },
                        {
                            "type": "linkedin",
                            "kind": "message_after_acceptance",
                            "step_id": "li-message",
                            "message": "Thanks for connecting",
                            "requires_linkedin_connected": True,
                            "cancel_on_gmail_reply": True,
                            "scheduled_at": "2026-04-30T09:00:00+05:30",
                        },
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    workflow_id = response.json()["workflow_ids"][0]

    detail = client.get(f"/api/outreach-workflows/{workflow_id}", headers=auth_headers())
    assert detail.status_code == 200
    payload = detail.json()
    assert len(payload["mail_jobs"]) == 2
    assert len(payload["linkedin_jobs"]) == 2
    assert payload["mail_jobs"][1]["parent_job_id"] == payload["mail_jobs"][0]["id"]


def test_workflow_allows_linkedin_only_contact(tmp_path):
    _, client = build_client(tmp_path)

    response = client.post(
        "/api/outreach-workflows/bulk",
        headers=auth_headers(),
        json={
            "workflows": [
                {
                    "contact": {
                        "recipient_name": "LinkedIn Only",
                        "linkedin_url": "https://www.linkedin.com/in/linkedin-only/",
                    },
                    "steps": [
                        {
                            "type": "linkedin",
                            "kind": "connection_request",
                            "step_id": "li-connect",
                            "scheduled_at": "2026-04-26T12:00:00+05:30",
                        }
                    ],
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    workflow_id = response.json()["workflow_ids"][0]
    detail = client.get(f"/api/outreach-workflows/{workflow_id}", headers=auth_headers())
    assert detail.status_code == 200
    assert detail.json()["recipient_email"] is None
    assert len(detail.json()["linkedin_jobs"]) == 1


def test_workflow_rejects_unknown_email_thread_parent(tmp_path):
    _, client = build_client(tmp_path)

    response = client.post(
        "/api/outreach-workflows/bulk",
        headers=auth_headers(),
        json={
            "workflows": [
                {
                    "contact": {
                        "recipient_email": "person@example.com",
                        "linkedin_url": "https://www.linkedin.com/in/person/",
                    },
                    "steps": [
                        {
                            "type": "email",
                            "step_id": "email-2",
                            "thread_parent_step_id": "missing",
                            "subject": "Follow up",
                            "body_text": "Body",
                            "scheduled_at": "2026-04-26T09:00:00+05:30",
                        }
                    ],
                }
            ]
        },
    )

    assert response.status_code == 400


def test_gmail_reply_blocks_pending_linkedin_message_job(tmp_path):
    app, client = build_client(tmp_path)
    response = client.post(
        "/api/outreach-workflows/bulk",
        headers=auth_headers(),
        json={
            "workflows": [
                {
                    "contact": {
                        "recipient_email": "person@example.com",
                        "recipient_name": "Person Example",
                        "linkedin_url": "https://www.linkedin.com/in/person/",
                    },
                    "steps": [
                        {
                            "type": "email",
                            "step_id": "email-1",
                            "subject": "Hello",
                            "body_text": "Body",
                            "scheduled_at": "2026-04-26T09:00:00+05:30",
                        },
                        {
                            "type": "linkedin",
                            "kind": "message_after_acceptance",
                            "step_id": "li-message",
                            "message": "Thanks for connecting",
                            "cancel_on_gmail_reply": True,
                            "scheduled_at": "2026-04-27T09:00:00+05:30",
                        },
                    ],
                }
            ]
        },
    )
    workflow_id = response.json()["workflow_ids"][0]
    detail = client.get(f"/api/outreach-workflows/{workflow_id}", headers=auth_headers()).json()
    mail_job_id = detail["mail_jobs"][0]["id"]
    linkedin_job_id = detail["linkedin_jobs"][0]["id"]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-26T04:00:00+00:00',
                message_id = '<mailtfoutofit.test@example.com>',
                gmail_thread_id = 'thread-1'
            WHERE id = ?
            """,
            (mail_job_id,),
        )
        payload = {
            "id": "gmail-inbound-1",
            "threadId": "thread-1",
            "labelIds": ["INBOX"],
            "snippet": "Reply",
            "internalDate": "1714100000000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "person@example.com"},
                    {"name": "Subject", "value": "Re: Hello"},
                    {"name": "In-Reply-To", "value": "<mailtfoutofit.test@example.com>"},
                    {"name": "References", "value": "<mailtfoutofit.test@example.com>"},
                ]
            },
        }
        assert app.state.service._process_inbound_message(connection, payload) is True

    linkedin_job = client.get(f"/api/linkedin-jobs/{linkedin_job_id}", headers=auth_headers()).json()
    workflow = client.get(f"/api/outreach-workflows/{workflow_id}", headers=auth_headers()).json()
    assert linkedin_job["status"] == "blocked"
    assert any(event["event_type"] == "gmail_replied" for event in workflow["events"])


def test_linkedin_connected_blocks_pending_email_marked_for_cancellation(tmp_path):
    app, client = build_client(tmp_path)
    response = client.post(
        "/api/outreach-workflows/bulk",
        headers=auth_headers(),
        json={
            "workflows": [
                {
                    "contact": {
                        "recipient_email": "person@example.com",
                        "recipient_name": "Person Example",
                        "linkedin_url": "https://www.linkedin.com/in/person/",
                    },
                    "steps": [
                        {
                            "type": "linkedin",
                            "kind": "connection_request",
                            "step_id": "li-connect",
                            "scheduled_at": "2026-04-26T09:00:00+05:30",
                        },
                        {
                            "type": "email",
                            "step_id": "email-3",
                            "subject": "Last note",
                            "body_text": "Body",
                            "cancel_on_linkedin_connected": True,
                            "scheduled_at": "2026-04-30T09:00:00+05:30",
                        },
                    ],
                }
            ]
        },
    )
    workflow_id = response.json()["workflow_ids"][0]
    workflow = client.get(f"/api/outreach-workflows/{workflow_id}", headers=auth_headers()).json()
    linkedin_job = workflow["linkedin_jobs"][0]
    email_job = workflow["mail_jobs"][0]

    app.state.service._openoutreach_request = lambda method, path, json_body=None: {
        "ok": True,
        "campaign_id": 1,
        "leads": [{
            "deal_id": 77,
            "public_identifier": "person",
            "state": "Connected",
            "linkedin_url": "https://www.linkedin.com/in/person/",
        }],
    } if method == "GET" else {
        "ok": True,
        "deal_id": 77,
        "public_identifier": "person",
        "pipeline_row": {
            "deal_id": 77,
            "public_identifier": "person",
            "state": "Connected",
        },
    }

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE linkedin_jobs
            SET openoutreach_public_identifier = 'person',
                openoutreach_campaign_id = '1'
            WHERE id = ?
            """,
            (linkedin_job["id"],),
        )

    app.state.service._sync_one_linkedin_job(linkedin_job)

    refreshed_workflow = client.get(f"/api/outreach-workflows/{workflow_id}", headers=auth_headers()).json()
    refreshed_email_job = next(job for job in refreshed_workflow["mail_jobs"] if job["id"] == email_job["id"])
    assert refreshed_email_job["status"] == "blocked"
    assert any(event["event_type"] == "linkedin_connected" for event in refreshed_workflow["events"])


def test_linkedin_dispatch_failure_sets_retry_metadata(tmp_path):
    app, client = build_client(tmp_path)
    response = client.post(
        "/api/outreach-workflows/bulk",
        headers=auth_headers(),
        json={
            "workflows": [
                {
                    "contact": {
                        "recipient_email": "person@example.com",
                        "linkedin_url": "https://www.linkedin.com/in/person/",
                    },
                    "steps": [
                        {
                            "type": "linkedin",
                            "kind": "connection_request",
                            "step_id": "li-connect",
                            "scheduled_at": "2026-04-26T09:00:00+05:30",
                        }
                    ],
                }
            ]
        },
    )
    workflow_id = response.json()["workflow_ids"][0]
    workflow = client.get(f"/api/outreach-workflows/{workflow_id}", headers=auth_headers()).json()
    linkedin_job_id = workflow["linkedin_jobs"][0]["id"]

    app.state.service._dispatch_linkedin_connection_job = lambda job: (_ for _ in ()).throw(RuntimeError("remote down"))

    result = app.state.service.dispatch_linkedin_job(linkedin_job_id)
    assert result["status"] == "failed"
    assert result["next_retry_at"] is not None
    assert "remote down" in result["last_error"]


def test_revive_failed_linkedin_jobs_requeues_recent_openoutreach_outage(tmp_path):
    app, client = build_client(tmp_path)
    response = client.post(
        "/api/outreach-workflows/bulk",
        headers=auth_headers(),
        json={
            "workflows": [
                {
                    "contact": {
                        "recipient_email": "person@example.com",
                        "linkedin_url": "https://www.linkedin.com/in/person/",
                    },
                    "steps": [
                        {
                            "type": "linkedin",
                            "kind": "connection_request",
                            "step_id": "li-connect",
                            "scheduled_at": "2026-04-26T09:00:00+05:30",
                        }
                    ],
                }
            ]
        },
    )
    workflow_id = response.json()["workflow_ids"][0]
    workflow = client.get(f"/api/outreach-workflows/{workflow_id}", headers=auth_headers()).json()
    linkedin_job_id = workflow["linkedin_jobs"][0]["id"]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE linkedin_jobs
            SET status = 'failed',
                next_retry_at = '2026-04-26T09:10:00+00:00',
                last_error = 'Connection refused',
                updated_at = '2026-04-26T09:10:00+00:00'
            WHERE id = ?
            """,
            (linkedin_job_id,),
        )

    app.state.service._openoutreach_request = lambda method, path, json_body=None: {
        "ok": True,
        "running": True,
    }

    result = app.state.service.revive_failed_linkedin_jobs()
    refreshed = client.get(f"/api/linkedin-jobs/{linkedin_job_id}", headers=auth_headers()).json()

    assert result["reachable"] is True
    assert result["revived_count"] == 1
    assert refreshed["status"] == "pending"
    assert refreshed["next_retry_at"] is None
    assert refreshed["last_error"] is None


def test_revive_failed_linkedin_jobs_skips_jobs_older_than_48_hours(tmp_path):
    app, client = build_client(tmp_path)
    response = client.post(
        "/api/outreach-workflows/bulk",
        headers=auth_headers(),
        json={
            "workflows": [
                {
                    "contact": {
                        "recipient_email": "person@example.com",
                        "linkedin_url": "https://www.linkedin.com/in/person/",
                    },
                    "steps": [
                        {
                            "type": "linkedin",
                            "kind": "connection_request",
                            "step_id": "li-connect",
                            "scheduled_at": "2026-04-26T09:00:00+05:30",
                        }
                    ],
                }
            ]
        },
    )
    workflow_id = response.json()["workflow_ids"][0]
    workflow = client.get(f"/api/outreach-workflows/{workflow_id}", headers=auth_headers()).json()
    linkedin_job_id = workflow["linkedin_jobs"][0]["id"]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE linkedin_jobs
            SET status = 'failed',
                created_at = '2026-04-20T09:00:00+00:00',
                next_retry_at = '2026-04-20T09:10:00+00:00',
                last_error = 'Connection refused',
                updated_at = '2026-04-20T09:10:00+00:00'
            WHERE id = ?
            """,
            (linkedin_job_id,),
        )

    app.state.service._openoutreach_request = lambda method, path, json_body=None: {
        "ok": True,
        "running": True,
    }

    result = app.state.service.revive_failed_linkedin_jobs()
    refreshed = client.get(f"/api/linkedin-jobs/{linkedin_job_id}", headers=auth_headers()).json()

    assert result["reachable"] is True
    assert result["revived_count"] == 0
    assert refreshed["status"] == "failed"
    assert refreshed["next_retry_at"] == "2026-04-20T09:10:00+00:00"
    assert refreshed["last_error"] == "Connection refused"

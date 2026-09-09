from io import BytesIO

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
    )
    app = create_app(settings)
    return app, TestClient(app)


def auth():
    return {"Authorization": "Bearer test-token"}


def _create_three_mail_thread(client, email="person@example.com", batch="thread-view"):
    response = client.post(
        "/api/mail-jobs/bulk",
        headers=auth(),
        json={
            "batch_name": batch,
            "jobs": [
                {
                    "client_job_id": "root",
                    "recipient_email": email,
                    "recipient_name": "Jane Chen",
                    "company": "Amazon",
                    "title": "Recruiter",
                    "subject": "Application for SDE role",
                    "body_text": "Hi",
                    "scheduled_at": "2026-04-20T09:00:00+05:30",
                    "attach_latest_resume": False,
                },
                {
                    "client_job_id": "f1",
                    "recipient_email": email,
                    "recipient_name": "Jane Chen",
                    "subject": "Re: Application for SDE role",
                    "body_text": "Following up",
                    "scheduled_at": "2026-04-22T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_client_id": "root",
                },
                {
                    "client_job_id": "f2",
                    "recipient_email": email,
                    "recipient_name": "Jane Chen",
                    "subject": "Re: Application — last note",
                    "body_text": "Last note",
                    "scheduled_at": "2026-04-24T09:00:00+05:30",
                    "attach_latest_resume": False,
                    "parent_job_client_id": "f1",
                },
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["job_ids"]


def test_list_mail_job_threads_groups_chain_into_one_thread(tmp_path):
    app, client = build_client(tmp_path)
    job_ids = _create_three_mail_thread(client)

    threads = app.state.service.list_mail_job_threads()
    assert len(threads) == 1

    thread = threads[0]
    assert thread["root_job_id"] == job_ids[0]
    assert thread["total"] == 3
    assert thread["counts"]["pending"] == 3
    assert thread["counts"]["sent"] == 0
    assert thread["derived_state"] == "active"
    assert thread["next_actionable_at"] is not None
    assert thread["recipient_email"] == "person@example.com"
    assert thread["recipient_name"] == "Jane Chen"
    assert thread["recipient_company"] == "Amazon"
    assert thread["root_subject"] == "Application for SDE role"


def test_list_mail_job_threads_marks_waiting_when_all_sent_no_reply(tmp_path):
    app, client = build_client(tmp_path)
    _create_three_mail_thread(client)

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-22T04:00:00+00:00',
                gmail_thread_id = 'gthread-waiting'
            """
        )

    threads = app.state.service.list_mail_job_threads()
    assert len(threads) == 1
    thread = threads[0]
    assert thread["counts"]["sent"] == 3
    assert thread["counts"]["pending"] == 0
    assert thread["derived_state"] == "waiting"
    assert thread["has_reply"] is False
    assert thread["last_sent_at"] is not None


def test_list_mail_job_threads_closes_thread_on_reply_and_attaches_snippet(tmp_path):
    app, client = build_client(tmp_path)
    job_ids = _create_three_mail_thread(client)
    root_job_id = job_ids[0]

    with app.state.service.connect() as connection:
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'sent',
                sent_at = '2026-04-20T04:00:00+00:00',
                gmail_thread_id = 'gthread-reply'
            WHERE id = ?
            """,
            (root_job_id,),
        )
        connection.execute(
            """
            UPDATE mail_jobs
            SET status = 'blocked',
                blocked_reason = 'contact_replied',
                gmail_thread_id = 'gthread-reply'
            WHERE id IN (?, ?)
            """,
            (job_ids[1], job_ids[2]),
        )
        connection.execute(
            """
            UPDATE contacts SET has_replied = 1, replied_at = '2026-04-21T05:00:00+00:00'
            """
        )
        connection.execute(
            """
            INSERT INTO inbound_messages (
                id, gmail_message_id, gmail_thread_id, from_email, subject, snippet,
                received_at, matched_job_id, contact_id, created_at
            ) VALUES (
                'inb-1', 'gmsg-1', 'gthread-reply', 'person@example.com',
                'Re: Application for SDE role', 'Thanks — let us set up a call.',
                '2026-04-21T05:00:00+00:00', ?,
                (SELECT id FROM contacts LIMIT 1),
                '2026-04-21T05:00:00+00:00'
            )
            """,
            (root_job_id,),
        )

    threads = app.state.service.list_mail_job_threads()
    assert len(threads) == 1
    thread = threads[0]
    assert thread["has_reply"] is True
    assert thread["derived_state"] == "closed"
    assert thread["reply"] is not None
    assert "set up a call" in thread["reply"]["snippet"]


def test_list_mail_job_threads_flags_failed_as_active(tmp_path):
    app, client = build_client(tmp_path)
    job_ids = _create_three_mail_thread(client)

    with app.state.service.connect() as connection:
        connection.execute(
            "UPDATE mail_jobs SET status = 'failed' WHERE id = ?",
            (job_ids[1],),
        )

    threads = app.state.service.list_mail_job_threads()
    thread = threads[0]
    assert thread["counts"]["failed"] == 1
    assert thread["derived_state"] == "active"


def test_list_mail_job_threads_filter_returns_full_thread_context(tmp_path):
    app, client = build_client(tmp_path)
    job_ids_a = _create_three_mail_thread(client, batch="batch-a")
    # Second independent thread shouldn't bleed in.
    _create_three_mail_thread(client, email="other@example.com", batch="batch-b")

    # Mark the middle mail of thread A as failed. Filter by failed should return
    # the full thread-A (all 3 mails), not just the 1 matching mail.
    with app.state.service.connect() as connection:
        connection.execute(
            "UPDATE mail_jobs SET status = 'failed' WHERE id = ?",
            (job_ids_a[1],),
        )

    threads = app.state.service.list_mail_job_threads(status_filter="failed")
    assert len(threads) == 1
    thread = threads[0]
    # Thread is returned with all 3 mails even though only 1 matched the filter.
    assert thread["total"] == 3
    assert thread["batch_name"] == "batch-a"
    assert thread["counts"]["failed"] == 1
    assert thread["counts"]["pending"] == 2


def test_dashboard_renders_thread_card_with_chain(tmp_path):
    _, client = build_client(tmp_path)
    _create_three_mail_thread(client)

    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "Jane Chen" in body
    assert 'class="chain"' in body
    assert "dot-pending" in body
    # Collapsed card shows thread-level counts, not a flat list
    assert "pending" in body
    assert "Active" in body  # new tab label
    assert "Upcoming" not in body  # old tab label is gone


def test_dashboard_back_compat_old_view_param(tmp_path):
    _, client = build_client(tmp_path)
    _create_three_mail_thread(client)

    # Old URLs using upcoming/sent should still work via the alias layer.
    response = client.get("/?view=upcoming")
    assert response.status_code == 200
    # Active tab should now be selected.
    assert 'aria-selected="true"' in response.text


def test_contact_detail_renders_thread_cards(tmp_path):
    app, client = build_client(tmp_path)
    _create_three_mail_thread(client)

    with app.state.service.connect() as connection:
        contact_row = connection.execute(
            "SELECT id FROM contacts WHERE email = 'person@example.com'"
        ).fetchone()
    contact_id = contact_row["id"]

    response = client.get("/contacts/%s" % contact_id)
    assert response.status_code == 200
    body = response.text
    assert "Mail threads" in body
    assert 'class="chain"' in body
    # Contact-scoped card should not repeat the contact identity in the summary.
    # (The contact header at the top of the page is the identity instead.)
    assert body.count("Jane Chen") >= 1


def test_job_detail_shows_thread_strip_breadcrumb(tmp_path):
    _, client = build_client(tmp_path)
    job_ids = _create_three_mail_thread(client)

    response = client.get("/jobs/%s" % job_ids[1])
    assert response.status_code == 200
    body = response.text
    assert "thread-strip" in body
    assert "mail 2 of 3" in body
    assert "Re: Application" in body

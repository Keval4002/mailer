import httpx, base64, sqlite3
from datetime import datetime, timedelta, timezone

db = sqlite3.connect('mailtfoutofit/data/mail_scheduler.db')
db.row_factory = sqlite3.Row
auth = db.execute("SELECT access_token FROM gmail_auth_state WHERE id=1").fetchone()
ACCESS_TOKEN = auth["access_token"]
db.close()

headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
GMAIL_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"

raw_bytes = b"To: ambanikeval2@gmail.com\r\nSubject: Test Schedule\r\n\r\nThis is a scheduled email test."
payload = {"message": {"raw": base64.urlsafe_b64encode(raw_bytes).decode("utf-8")}}

with httpx.Client() as client:
    draft_r = client.post(f"{GMAIL_ROOT}/drafts", headers=headers, json=payload)
    draft_id = draft_r.json()["id"]
    print("Draft ID:", draft_id)
    
    # Schedule for 1 hour from now
    deliver_at = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # Try the undocumented scheduleTime
    send_r = client.post(
        f"{GMAIL_ROOT}/drafts/send",
        headers=headers,
        json={"id": draft_id, "scheduleTime": deliver_at}
    )
    print("Send Response:", send_r.status_code, send_r.text)

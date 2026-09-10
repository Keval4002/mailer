"""
Deletes all drafts from Gmail that were created by our scheduler
(subject starts with 'Application:' or is empty / follow-up).
Run once to clean up orphaned drafts.
"""
import httpx

ACCESS_TOKEN = None

# Get token from DB
import sqlite3
db = sqlite3.connect('mailtfoutofit/data/mail_scheduler.db')
db.row_factory = sqlite3.Row
auth = db.execute("SELECT access_token FROM gmail_auth_state WHERE id=1").fetchone()
if not auth:
    print("No Gmail auth found!")
    exit(1)
ACCESS_TOKEN = auth["access_token"]
db.close()

GMAIL_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}

# List all drafts
with httpx.Client(timeout=20) as client:
    r = client.get(f"{GMAIL_ROOT}/drafts", headers=headers, params={"maxResults": 50})
    data = r.json()
    drafts = data.get("drafts", [])
    print(f"Found {len(drafts)} draft(s) in Gmail")

    deleted = 0
    for draft in drafts:
        draft_id = draft["id"]
        # Get draft details to check subject
        dr = client.get(f"{GMAIL_ROOT}/drafts/{draft_id}", headers=headers, params={"format": "metadata", "metadataHeaders": ["Subject", "To"]})
        meta = dr.json()
        headers_list = meta.get("message", {}).get("payload", {}).get("headers", [])
        subject = next((h["value"] for h in headers_list if h["name"] == "Subject"), "")
        to = next((h["value"] for h in headers_list if h["name"] == "To"), "")
        print(f"  Draft {draft_id[:12]}... To: {to[:40]} Subject: {subject[:60]}")

        # Delete it
        del_r = client.delete(f"{GMAIL_ROOT}/drafts/{draft_id}", headers=headers)
        if del_r.status_code in (200, 204, 404):
            print(f"    [OK] Deleted")
            deleted += 1
        else:
            print(f"    [FAIL] {del_r.status_code} {del_r.text[:100]}")

print(f"\nDeleted {deleted}/{len(drafts)} drafts.")

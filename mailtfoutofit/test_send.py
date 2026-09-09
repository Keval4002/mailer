import httpx
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# Read from .env or fallback
API_TOKEN = os.getenv("API_BEARER_TOKEN", "change-me")

# Schedule for 1 minute from now
scheduled_time = (datetime.now(timezone.utc) + timedelta(minutes=1)).astimezone().isoformat()

payload = {
    "batch_name": "System Test",
    "jobs": [
        {
            "recipient_email": "hello@example.com",
            "subject": "Hello from Mail Scheduler!",
            "body_text": "It works! The Gmail OAuth is fully operational and the scheduler successfully dispatched this email.",
            "scheduled_at": scheduled_time,
            "attach_latest_resume": False
        }
    ]
}

print("Ingesting job into the scheduler...")
response = httpx.post(
    "http://127.0.0.1:8000/api/mail-jobs/bulk",
    json=payload,
    headers={"Authorization": f"Bearer {API_TOKEN}"}
)

print(f"Status: {response.status_code}")
print(response.json())

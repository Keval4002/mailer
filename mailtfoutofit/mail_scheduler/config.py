import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _to_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    api_bearer_token: str
    database_path: str
    files_dir: str
    mail_provider: str = "gmail_api"
    mail_from: str = ""
    public_base_url: str = ""
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_redirect_uri: str = ""
    gmail_poll_seconds: int = 60
    gmail_watch_enabled: bool = False
    scheduler_poll_seconds: int = 60
    scheduler_enabled: bool = True
    openoutreach_base_url: str = ""
    openoutreach_api_token: str = ""
    openoutreach_campaign_id: str = ""
    openoutreach_database_url: str = ""
    openoutreach_auto_start_daemon: bool = False
    linkedin_retry_seconds: int = 300
    linkedin_status_sync_seconds: int = 300

    @classmethod
    def from_env(cls):
        return cls(
            api_bearer_token=os.getenv("API_BEARER_TOKEN", "change-me"),
            database_path=os.getenv("DATABASE_PATH", "./data/mail_scheduler.db"),
            files_dir=os.getenv("FILES_DIR", "./data/files"),
            mail_provider=os.getenv("MAIL_PROVIDER", "gmail_api"),
            mail_from=os.getenv("MAIL_FROM", ""),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
            gmail_client_id=os.getenv("GMAIL_CLIENT_ID", ""),
            gmail_client_secret=os.getenv("GMAIL_CLIENT_SECRET", ""),
            gmail_redirect_uri=os.getenv("GMAIL_REDIRECT_URI", ""),
            gmail_poll_seconds=int(os.getenv("GMAIL_POLL_SECONDS", "60")),
            gmail_watch_enabled=_to_bool(os.getenv("GMAIL_WATCH_ENABLED"), False),
            scheduler_poll_seconds=int(os.getenv("SCHEDULER_POLL_SECONDS", "60")),
            scheduler_enabled=_to_bool(os.getenv("SCHEDULER_ENABLED"), True),
            openoutreach_base_url=os.getenv("OPENOUTREACH_BASE_URL", "").rstrip("/"),
            openoutreach_api_token=os.getenv("OPENOUTREACH_API_TOKEN", ""),
            openoutreach_campaign_id=os.getenv("OPENOUTREACH_CAMPAIGN_ID", ""),
            openoutreach_database_url=os.getenv("OPENOUTREACH_DATABASE_URL", ""),
            openoutreach_auto_start_daemon=_to_bool(os.getenv("OPENOUTREACH_AUTO_START_DAEMON"), False),
            linkedin_retry_seconds=int(os.getenv("LINKEDIN_RETRY_SECONDS", "300")),
            linkedin_status_sync_seconds=int(os.getenv("LINKEDIN_STATUS_SYNC_SECONDS", "300")),
        )

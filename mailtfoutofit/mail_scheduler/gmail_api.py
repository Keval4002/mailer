import base64
from typing import Dict, List, Optional
from urllib.parse import urlencode

import httpx


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


class GmailApiError(Exception):
    pass


def build_authorization_url(client_id: str, redirect_uri: str, state: str):
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
    )
    return "%s?%s" % (GOOGLE_AUTH_URL, query)


def _json_or_raise(response: httpx.Response):
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text
        raise GmailApiError("Gmail API request failed: %s" % body) from exc
    return response.json()


def exchange_code_for_tokens(client_id: str, client_secret: str, redirect_uri: str, code: str):
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
    return _json_or_raise(response)


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str):
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    return _json_or_raise(response)


def _auth_headers(access_token: str):
    return {"Authorization": "Bearer %s" % access_token}


def get_profile(access_token: str):
    with httpx.Client(timeout=20.0) as client:
        response = client.get("%s/profile" % GMAIL_API_ROOT, headers=_auth_headers(access_token))
    return _json_or_raise(response)


def send_message(access_token: str, raw_bytes: bytes, thread_id: Optional[str] = None):
    payload: Dict[str, object] = {"raw": base64.urlsafe_b64encode(raw_bytes).decode("utf-8")}
    if thread_id:
        payload["threadId"] = thread_id
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            "%s/messages/send" % GMAIL_API_ROOT,
            headers=_auth_headers(access_token),
            json=payload,
        )
    return _json_or_raise(response)


def list_history(access_token: str, start_history_id: str):
    history: List[Dict] = []
    page_token = None
    with httpx.Client(timeout=20.0) as client:
        while True:
            params = {"startHistoryId": start_history_id, "historyTypes": "messageAdded"}
            if page_token:
                params["pageToken"] = page_token
            response = client.get("%s/history" % GMAIL_API_ROOT, headers=_auth_headers(access_token), params=params)
            payload = _json_or_raise(response)
            history.extend(payload.get("history", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                return {
                    "history": history,
                    "historyId": payload.get("historyId"),
                }


def list_recent_messages(access_token: str, max_results: int = 25):
    with httpx.Client(timeout=20.0) as client:
        response = client.get(
            "%s/messages" % GMAIL_API_ROOT,
            headers=_auth_headers(access_token),
            params={"labelIds": "INBOX", "maxResults": max_results},
        )
    return _json_or_raise(response)


def get_message_metadata(access_token: str, gmail_message_id: str):
    with httpx.Client(timeout=20.0) as client:
        response = client.get(
            "%s/messages/%s" % (GMAIL_API_ROOT, gmail_message_id),
            headers=_auth_headers(access_token),
            params={
                "format": "metadata",
                "metadataHeaders": ["Message-ID", "In-Reply-To", "References", "From", "Subject", "Date"],
            },
        )
    return _json_or_raise(response)


def get_thread(access_token: str, gmail_thread_id: str):
    with httpx.Client(timeout=20.0) as client:
        response = client.get(
            "%s/threads/%s" % (GMAIL_API_ROOT, gmail_thread_id),
            headers=_auth_headers(access_token),
            params={
                "format": "metadata",
                "metadataHeaders": ["Message-ID", "In-Reply-To", "References", "From", "Subject", "Date"],
            },
        )
    return _json_or_raise(response)

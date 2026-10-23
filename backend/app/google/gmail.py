import base64
import logging
from email.mime.text import MIMEText
from googleapiclient.discovery import build
from app.google.auth import get_credentials

logger = logging.getLogger("pa.google.gmail")


_MAX_BODY_CHARS = 8000


def read_emails(chat_id: str, max_results: int = 5, query: str = "") -> str:
    """Latest inbox emails, or — with `query` (Gmail search syntax) — matching
    emails from any folder. Each result carries its id for read_message."""
    creds = get_credentials(chat_id)
    if not creds:
        return "Gmail not connected. Use /auth/google/start to connect."

    service = build("gmail", "v1", credentials=creds)
    max_results = max(1, min(int(max_results or 5), 25))
    params = {"userId": "me", "maxResults": max_results}
    if query:
        params["q"] = query
    else:
        params["labelIds"] = ["INBOX"]
    result = service.users().messages().list(**params).execute()

    messages = result.get("messages", [])
    if not messages:
        return f"No emails found for {query!r}." if query else "No emails found."

    summaries = []
    for msg in messages:
        detail = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]
        ).execute()
        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        snippet = detail.get("snippet", "")[:200]
        summaries.append(
            f"Id: {msg['id']}\n"
            f"Date: {headers.get('Date', '')}\n"
            f"From: {headers.get('From', 'Unknown')}\n"
            f"Subject: {headers.get('Subject', '(no subject)')}\n"
            f"Preview: {snippet}"
        )

    return "\n---\n".join(summaries)


def send_email(chat_id: str, to: str, subject: str, body: str) -> str:
    creds = get_credentials(chat_id)
    if not creds:
        return "Gmail not connected. Use /auth/google/start to connect."

    service = build("gmail", "v1", credentials=creds)
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    logger.info("Email sent to %s for chat_id=%s", to, chat_id)
    return f"Email sent to {to} with subject '{subject}'."


def _plain_text(payload: dict) -> str:
    """Best plain-text body of a Gmail message payload (text/plain, else
    tag-stripped text/html), walking multipart parts."""
    import re

    def walk(part: dict, mime: str) -> str:
        if part.get("mimeType") == mime and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
        for sub in part.get("parts", []) or []:
            found = walk(sub, mime)
            if found:
                return found
        return ""

    text = walk(payload, "text/plain")
    if not text:
        html = walk(payload, "text/html")
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def read_message(chat_id: str, message_id: str) -> str:
    """Full text of one email (headers + body, capped)."""
    creds = get_credentials(chat_id)
    if not creds:
        return "Gmail not connected. Use /auth/google/start to connect."

    service = build("gmail", "v1", credentials=creds)
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = msg.get("payload", {})
    headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
    body = _plain_text(payload) or msg.get("snippet", "")
    if len(body) > _MAX_BODY_CHARS:
        body = body[:_MAX_BODY_CHARS] + "\n… (truncated)"
    return (
        f"From: {headers.get('From', 'Unknown')}\n"
        f"To: {headers.get('To', '')}\n"
        f"Date: {headers.get('Date', '')}\n"
        f"Subject: {headers.get('Subject', '(no subject)')}\n\n{body}"
    )

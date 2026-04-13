import base64
import logging
from email.mime.text import MIMEText
from googleapiclient.discovery import build
from app.google.auth import get_credentials

logger = logging.getLogger("pa.google.gmail")


def read_emails(chat_id: str, max_results: int = 5) -> str:
    creds = get_credentials(chat_id)
    if not creds:
        return "Gmail not connected. Use /auth/google/start to connect."

    service = build("gmail", "v1", credentials=creds)
    result = service.users().messages().list(
        userId="me", maxResults=max_results, labelIds=["INBOX"]
    ).execute()

    messages = result.get("messages", [])
    if not messages:
        return "No emails found."

    summaries = []
    for msg in messages:
        detail = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject"]
        ).execute()
        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        snippet = detail.get("snippet", "")[:200]
        summaries.append(
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

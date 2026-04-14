from langchain_core.tools import tool
from app.google.gmail import read_emails, send_email
from app.google.calendar import list_events, create_event
from app.google.auth import get_auth_url, get_credentials
from app.google.drive_tools import get_drive_tools


def get_google_tools(chat_id: str) -> list:
    @tool
    def google_connect() -> str:
        """Connect Google account (Gmail + Calendar + Drive). Call this when the user wants to link, connect, or authorize Google/Gmail/Calendar/Drive."""
        creds = get_credentials(chat_id)
        if creds and creds.valid:
            return "Google account is already connected."
        url = get_auth_url(chat_id)
        return f"Open this link to connect your Google account:\n{url}"

    @tool
    def gmail_read(max_results: int = 5) -> str:
        """Read the latest emails from Gmail inbox."""
        return read_emails(chat_id, max_results)

    @tool
    def gmail_send(to: str, subject: str, body: str) -> str:
        """Send an email via Gmail. Args: to (address), subject, body."""
        return send_email(chat_id, to, subject, body)

    @tool
    def calendar_list(max_results: int = 5) -> str:
        """List upcoming Google Calendar events."""
        return list_events(chat_id, max_results)

    @tool
    def calendar_create(title: str, start_datetime: str, end_datetime: str, attendees: str = "") -> str:
        """Create a Google Calendar event. Datetimes in ISO 8601. Attendees: comma-separated emails."""
        return create_event(chat_id, title, start_datetime, end_datetime, attendees)

    return [
        google_connect,
        gmail_read,
        gmail_send,
        calendar_list,
        calendar_create,
    ] + get_drive_tools(chat_id)

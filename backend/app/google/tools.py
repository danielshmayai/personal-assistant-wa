from langchain_core.tools import tool
from app.google.gmail import read_emails, read_message, send_email
from app.google.calendar import create_event, delete_event, list_events, update_event
from app.google.auth import get_auth_url, get_credentials
from app.google.drive_tools import get_drive_tools
from app.google.maps_tool import get_maps_tools


def get_google_tools(chat_id: str) -> list:
    @tool
    def google_connect() -> str:
        """Connect Google account (Gmail + Calendar + Drive). Call this when the user wants to link, connect, or authorize Google/Gmail/Calendar/Drive.

        ALWAYS call this tool to obtain the link, every single time one is asked
        for. Each link is single-use and EXPIRES AFTER 1 HOUR. NEVER repeat or
        copy a Google auth URL that appears earlier in this conversation — a
        reused link fails with an expired-state error. Call the tool again to
        mint a fresh one.
        """
        from app.google.drive_tools import _DRIVE_SCOPE
        creds = get_credentials(chat_id)
        has_drive = (
            creds
            and creds.valid
            and creds.scopes
            and any("drive" in s for s in creds.scopes)
        )
        if has_drive:
            return "Google account is already connected (Gmail, Calendar, and Drive)."
        # Either not connected at all, or connected without Drive scope
        url = get_auth_url(chat_id)
        expiry_note = (
            "\n\n(This link is valid for 1 hour and can only be used once. "
            "If it expires, ask me for a new one.)"
        )
        if creds and creds.valid:
            return (
                "Your Google account is connected but Drive access is missing. "
                "Please open this link to reconnect and grant Drive permission:\n"
                + url + expiry_note
            )
        return f"Open this link to connect your Google account:\n{url}{expiry_note}"

    @tool
    def gmail_read(max_results: int = 5, query: str = "") -> str:
        """List emails: the latest in the inbox, or with `query` search all mail
        using Gmail syntax (from:dana subject:invoice newer_than:7d has:attachment).
        Returns id, date, sender, subject and a preview for each."""
        return read_emails(chat_id, max_results, query)

    @tool
    def gmail_read_message(message_id: str) -> str:
        """Read one email in full (body text). message_id comes from gmail_read."""
        return read_message(chat_id, message_id)

    @tool
    def gmail_send(to: str, subject: str, body: str) -> str:
        """Send an email via Gmail. Args: to (address), subject, body."""
        return send_email(chat_id, to, subject, body)

    @tool
    def calendar_list(max_results: int = 10, days_ahead: int = 0, query: str = "") -> str:
        """List upcoming calendar events with their ids. days_ahead limits the range
        (1 = today/tomorrow window, 7 = this week); query matches event text."""
        return list_events(chat_id, max_results, days_ahead, query)

    @tool
    def calendar_update(event_id: str, title: str = "", start_datetime: str = "",
                        end_datetime: str = "", attendees: str = "", location: str = "",
                        description: str = "") -> str:
        """Change an existing event — only the fields you pass. Moving the start
        keeps its duration unless end_datetime is given. ISO 8601 times; get
        event_id from calendar_list first."""
        return update_event(chat_id, event_id, title, start_datetime, end_datetime,
                            attendees, location, description)

    @tool
    def calendar_delete(event_id: str) -> str:
        """Delete an event (cancel a meeting). Get event_id from calendar_list first;
        confirm with the user if it's unclear which event they mean."""
        return delete_event(chat_id, event_id)

    @tool
    def calendar_create(title: str, start_datetime: str, end_datetime: str, attendees: str = "") -> str:
        """Create a Google Calendar event. Datetimes in ISO 8601. Attendees: comma-separated emails."""
        return create_event(chat_id, title, start_datetime, end_datetime, attendees)

    return [
        google_connect,
        gmail_read,
        gmail_read_message,
        gmail_send,
        calendar_list,
        calendar_create,
        calendar_update,
        calendar_delete,
    ] + get_drive_tools(chat_id) + get_maps_tools(chat_id)


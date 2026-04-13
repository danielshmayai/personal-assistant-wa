import logging
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build
from app.google.auth import get_credentials
from app.config import USER_TIMEZONE

logger = logging.getLogger("pa.google.calendar")


def list_events(chat_id: str, max_results: int = 5) -> str:
    creds = get_credentials(chat_id)
    if not creds:
        return "Google Calendar not connected. Use /auth/google/start to connect."

    service = build("calendar", "v3", credentials=creds)
    now = datetime.now(timezone.utc).isoformat()
    result = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = result.get("items", [])
    if not events:
        return "No upcoming events found."

    summaries = []
    for e in events:
        start = e["start"].get("dateTime", e["start"].get("date", ""))
        attendees = ", ".join(
            a.get("email", "") for a in e.get("attendees", [])
        ) or "none"
        summaries.append(
            f"Title: {e.get('summary', '(no title)')}\n"
            f"Start: {start}\n"
            f"Attendees: {attendees}"
        )

    return "\n---\n".join(summaries)


def create_event(
    chat_id: str,
    title: str,
    start_datetime: str,
    end_datetime: str,
    attendees: str = "",
) -> str:
    creds = get_credentials(chat_id)
    if not creds:
        return "Google Calendar not connected. Use /auth/google/start to connect."

    if not start_datetime:
        return "Could not create event: start time is missing."

    # Default end to 1 hour after start if not provided
    if not end_datetime:
        try:
            start_dt = datetime.fromisoformat(start_datetime)
            end_datetime = (start_dt + timedelta(hours=1)).isoformat()
        except ValueError:
            end_datetime = start_datetime

    service = build("calendar", "v3", credentials=creds)
    body = {
        "summary": title or "Meeting",
        "start": {"dateTime": start_datetime, "timeZone": USER_TIMEZONE},
        "end": {"dateTime": end_datetime, "timeZone": USER_TIMEZONE},
    }
    if attendees:
        body["attendees"] = [{"email": e.strip()} for e in attendees.split(",") if e.strip()]

    event = service.events().insert(calendarId="primary", body=body).execute()
    logger.info("Calendar event created: %s for chat_id=%s", event.get("id"), chat_id)
    return f"Event '{title}' created from {start_datetime} to {end_datetime}."

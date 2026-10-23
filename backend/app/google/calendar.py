import logging
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build
from app.google.auth import get_credentials
from app.config import USER_TIMEZONE

logger = logging.getLogger("pa.google.calendar")


def list_events(chat_id: str, max_results: int = 5, days_ahead: int = 0, query: str = "") -> str:
    """Upcoming events (optionally within `days_ahead` days, or matching
    `query` text). Each result carries its id for update/delete."""
    creds = get_credentials(chat_id)
    if not creds:
        return "Google Calendar not connected. Use /auth/google/start to connect."

    service = build("calendar", "v3", credentials=creds)
    now = datetime.now(timezone.utc)
    params = {
        "calendarId": "primary",
        "timeMin": now.isoformat(),
        "maxResults": max(1, min(int(max_results or 5), 50)),
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if days_ahead and days_ahead > 0:
        params["timeMax"] = (now + timedelta(days=int(days_ahead))).isoformat()
    if query:
        params["q"] = query
    result = service.events().list(**params).execute()

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
            f"Id: {e.get('id', '')}\n"
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


def update_event(
    chat_id: str,
    event_id: str,
    title: str = "",
    start_datetime: str = "",
    end_datetime: str = "",
    attendees: str = "",
    location: str = "",
    description: str = "",
) -> str:
    """Change only the given fields of an event (patch). Moving the start
    without an end keeps the event's duration."""
    creds = get_credentials(chat_id)
    if not creds:
        return "Google Calendar not connected. Use /auth/google/start to connect."

    service = build("calendar", "v3", credentials=creds)
    body: dict = {}
    if title:
        body["summary"] = title
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    if attendees:
        body["attendees"] = [{"email": e.strip()} for e in attendees.split(",") if e.strip()]
    if start_datetime:
        body["start"] = {"dateTime": start_datetime, "timeZone": USER_TIMEZONE}
        if not end_datetime:
            current = service.events().get(calendarId="primary", eventId=event_id).execute()
            try:
                old_start = datetime.fromisoformat(current["start"]["dateTime"])
                old_end = datetime.fromisoformat(current["end"]["dateTime"])
                end_datetime = (datetime.fromisoformat(start_datetime) + (old_end - old_start)).isoformat()
            except (KeyError, ValueError):
                end_datetime = (datetime.fromisoformat(start_datetime) + timedelta(hours=1)).isoformat()
    if end_datetime:
        body["end"] = {"dateTime": end_datetime, "timeZone": USER_TIMEZONE}
    if not body:
        return "Nothing to change — pass at least one field to update."

    event = service.events().patch(calendarId="primary", eventId=event_id, body=body).execute()
    logger.info("Calendar event updated: %s for chat_id=%s", event_id, chat_id)
    start = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", ""))
    return f"Event '{event.get('summary', '')}' updated (starts {start})."


def delete_event(chat_id: str, event_id: str) -> str:
    creds = get_credentials(chat_id)
    if not creds:
        return "Google Calendar not connected. Use /auth/google/start to connect."

    service = build("calendar", "v3", credentials=creds)
    service.events().delete(calendarId="primary", eventId=event_id).execute()
    logger.info("Calendar event deleted: %s for chat_id=%s", event_id, chat_id)
    return "Event deleted."

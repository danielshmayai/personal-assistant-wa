"""LangChain tools for scheduling future actions.

get_schedule_tools(chat_id) returns three tools bound to the caller's chat_id:
  schedule_action   — create a deferred job (tuya_command or send_message)
  list_scheduled    — show the user's pending jobs
  cancel_scheduled  — cancel a pending job by its ID
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from app.config import USER_TIMEZONE


def _parse_run_at(delay_minutes: int, run_at_iso: str) -> datetime:
    tz = ZoneInfo(USER_TIMEZONE)
    if run_at_iso:
        try:
            dt = datetime.fromisoformat(run_at_iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc) + timedelta(minutes=max(1, delay_minutes))


def get_schedule_tools(chat_id: str) -> list:
    """Return schedule tools bound to *chat_id*."""

    @tool
    def schedule_action(
        action_type: str,
        payload: dict,
        delay_minutes: int = 0,
        run_at_iso: str = "",
    ) -> str:
        """Schedule a future action to run automatically — no user interaction needed.

        Use this whenever the user says things like:
          "עוד 5 דקות תדליק את…" / "in 5 minutes turn on…"
          "בשעה 21:00 כבה את…" / "at 9 PM shut off…"
          "תזכיר לי בעוד שעה ש…" / "remind me in an hour that…"

        action_type: one of 'tuya_command' or 'send_message'

        payload for tuya_command:
          {
            "device_id":   "<tuya device id>",
            "commands":    {"switch_1": true},   ← same format as control_device
            "description": "הדלקת דוד חימום"    ← human-readable label
          }

        payload for send_message:
          {
            "text": "תזכורת: תבדוק את הכביסה"
          }

        delay_minutes: minutes from now (use this for relative times like "in 5 minutes").
                       Must be ≥ 1.
        run_at_iso:    absolute ISO-8601 datetime — use instead of delay_minutes when
                       the user specifies a clock time like "at 21:00".
                       If no timezone is given, USER_TIMEZONE is assumed.

        Returns a confirmation string with the job ID and scheduled time.
        """
        from app.scheduled_jobs import insert_job

        if action_type not in ("tuya_command", "send_message"):
            return (
                f"סוג פעולה לא מוכר: '{action_type}'. "
                "השתמש ב-'tuya_command' או 'send_message'."
            )

        if action_type == "tuya_command":
            if not payload.get("device_id") or not payload.get("commands"):
                return "חסרים שדות: payload חייב לכלול device_id ו-commands."

        if action_type == "send_message":
            if not payload.get("text"):
                return "חסר שדה text ב-payload."

        if delay_minutes < 1 and not run_at_iso:
            return "יש לציין delay_minutes (≥1) או run_at_iso."

        run_at = _parse_run_at(delay_minutes, run_at_iso)
        job_id = insert_job(chat_id, action_type, payload, run_at)

        tz = ZoneInfo(USER_TIMEZONE)
        local_time = run_at.astimezone(tz).strftime("%H:%M")
        return f"✅ תזמנתי (מזהה #{job_id}) לביצוע בשעה {local_time}."

    @tool
    def list_scheduled() -> str:
        """Show all pending scheduled actions for this chat.

        Use when the user asks "מה מתוזמן?" or "יש לי משהו מתוזמן?" or
        "what did I schedule?".
        """
        from app.scheduled_jobs import list_pending_jobs

        jobs = list_pending_jobs(chat_id)
        if not jobs:
            return "אין פעולות מתוזמנות כרגע."

        tz = ZoneInfo(USER_TIMEZONE)
        lines = ["**פעולות מתוזמנות:**"]
        for j in jobs:
            local_time = j["run_at"].astimezone(tz).strftime("%H:%M")
            desc = j["payload"].get("description") or j["payload"].get("text", j["action_type"])
            lines.append(f"• #{j['id']} — {desc} (בשעה {local_time})")
        return "\n".join(lines)

    @tool
    def cancel_scheduled(job_id: int) -> str:
        """Cancel a pending scheduled action by its ID.

        Use when the user says "בטל את הפעולה #3" or "cancel job 5".
        Get the ID from list_scheduled first if needed.
        """
        from app.scheduled_jobs import cancel_job

        ok = cancel_job(job_id, chat_id)
        if ok:
            return f"✅ ביטלתי את הפעולה #{job_id}."
        return f"לא מצאתי פעולה ממתינה עם מזהה #{job_id}."

    return [schedule_action, list_scheduled, cancel_scheduled]

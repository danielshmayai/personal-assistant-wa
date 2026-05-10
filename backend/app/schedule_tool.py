"""LangChain tools for scheduling future actions.

get_schedule_tools(chat_id) returns four tools bound to the caller's chat_id.
All parameters use primitive types (str/int) — no dict params, which break
Gemini's function-calling schema generation.
"""

import json
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
    def schedule_tuya_command(
        device_id: str,
        commands_json: str,
        description: str,
        delay_minutes: int = 0,
        run_at_iso: str = "",
    ) -> str:
        """Schedule a Tuya smart-home command to run at a future time.

        Use whenever the user says "turn on in 5 minutes", "shut off at 22:00",
        "עוד 5 דקות תדליק את…", etc.

        device_id: Tuya device ID (call list_tuya_devices first if unknown)
        commands_json: device commands as a JSON string, e.g. '{"switch_1": true}'
        description: short human-readable label, e.g. "הדלקת דוד חימום"
        delay_minutes: minutes from now (≥1); use for relative times like "in 5 minutes"
        run_at_iso: absolute time like "21:00" or "2025-05-10T21:00"; use for clock times
        """
        from app.scheduled_jobs import insert_job

        if not device_id:
            return "חסר device_id — קרא ל-list_tuya_devices כדי לקבל את המזהה."

        try:
            commands = json.loads(commands_json) if isinstance(commands_json, str) else commands_json
        except json.JSONDecodeError:
            return f"commands_json לא תקין: {commands_json}"

        if delay_minutes < 1 and not run_at_iso:
            return "יש לציין delay_minutes (≥1) או run_at_iso."

        run_at = _parse_run_at(delay_minutes, run_at_iso)
        payload = {
            "device_id": device_id,
            "commands": commands,
            "description": description or f"פקודה ל-{device_id[:8]}",
        }
        job_id = insert_job(chat_id, "tuya_command", payload, run_at)
        tz = ZoneInfo(USER_TIMEZONE)
        local_time = run_at.astimezone(tz).strftime("%H:%M")
        return f"✅ תזמנתי '{description}' לביצוע בשעה {local_time} (מזהה #{job_id})."

    @tool
    def schedule_reminder(
        text: str,
        delay_minutes: int = 0,
        run_at_iso: str = "",
    ) -> str:
        """Schedule a reminder message to be sent to you at a future time.

        Use for "remind me in 30 minutes that…", "alert me at 9 PM",
        "תזכיר לי בעוד שעה ש…", etc.

        text: the reminder message to send
        delay_minutes: minutes from now (≥1); use for relative times
        run_at_iso: absolute time like "21:00" or ISO datetime
        """
        from app.scheduled_jobs import insert_job

        if not text:
            return "חסר text לתזכורת."
        if delay_minutes < 1 and not run_at_iso:
            return "יש לציין delay_minutes (≥1) או run_at_iso."

        run_at = _parse_run_at(delay_minutes, run_at_iso)
        job_id = insert_job(chat_id, "send_message", {"text": text}, run_at)
        tz = ZoneInfo(USER_TIMEZONE)
        local_time = run_at.astimezone(tz).strftime("%H:%M")
        return f"✅ תזכורת נקבעה לשעה {local_time} (מזהה #{job_id})."

    @tool
    def list_scheduled() -> str:
        """Show all pending scheduled actions for this chat.

        Use when the user asks "מה מתוזמן?", "יש לי משימות?", "what did I schedule?".
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
        """Cancel a pending scheduled action by its numeric ID.

        Use when the user says "בטל פעולה #3", "cancel job 5".
        Call list_scheduled first to get the ID if needed.
        """
        from app.scheduled_jobs import cancel_job

        ok = cancel_job(job_id, chat_id)
        if ok:
            return f"✅ ביטלתי את הפעולה #{job_id}."
        return f"לא מצאתי פעולה ממתינה עם מזהה #{job_id}."

    return [schedule_tuya_command, schedule_reminder, list_scheduled, cancel_scheduled]

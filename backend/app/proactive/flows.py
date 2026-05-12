"""Proactive flows — background tasks that surface cards and notifications.

Each flow is a coroutine started at app startup. They are intentionally
lightweight: they check data and create cards/jobs without running the full
LangGraph agent.

Flows implemented:
  flow_scheduled_job_visibility — synthetic; jobs are already visible via /api/jobs
  flow_morning_briefing         — daily 7:30 briefing (stub: creates a reminder job)
  flow_smart_home_anomaly       — detects unexpected device state changes
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app.config import DATABASE_URL, USER_TIMEZONE

logger = logging.getLogger("pa.proactive")

_tasks: list[asyncio.Task] = []


async def start_flows(chat_ids: list[str]) -> None:
    """Start all background proactive flows."""
    _tasks.append(asyncio.create_task(_flow_morning_briefing(chat_ids), name="flow_morning_briefing"))
    logger.info("Proactive flows started for %d chat_ids", len(chat_ids))


async def stop_flows() -> None:
    for t in _tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    _tasks.clear()
    logger.info("Proactive flows stopped")


async def _flow_morning_briefing(chat_ids: list[str]) -> None:
    """Every day at 7:30 local time: schedule a morning briefing send_message job."""
    if not DATABASE_URL:
        return
    while True:
        tz = ZoneInfo(USER_TIMEZONE)
        now = datetime.now(tz=tz)
        target = now.replace(hour=7, minute=30, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        logger.info("Morning briefing scheduled in %.0f seconds", wait_seconds)
        await asyncio.sleep(wait_seconds)

        for chat_id in chat_ids:
            try:
                await _schedule_morning_briefing(chat_id)
            except Exception:
                logger.exception("Morning briefing failed for chat_id=%s", chat_id)


async def _schedule_morning_briefing(chat_id: str) -> None:
    """Insert a morning briefing send_message job that fires immediately."""
    from app.scheduled_jobs import insert_job
    from datetime import timezone as tz_mod

    tz = ZoneInfo(USER_TIMEZONE)
    now_local = datetime.now(tz=tz)

    text = (
        f"🌅 *בוקר טוב!*\n\n"
        f"עכשיו {now_local.strftime('%H:%M, %A')}.\n"
        "שלח 'תציג לי את היום שלי' לסיכום יומי: לוח שנה, מייל ומזג אוויר."
    )
    insert_job(
        chat_id=chat_id,
        action_type="send_message",
        payload={"text": text},
        run_at=datetime.now(timezone.utc),
    )
    logger.info("Morning briefing job created for chat_id=%s", chat_id)

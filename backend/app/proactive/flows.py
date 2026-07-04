"""Proactive flows — background tasks that fire on a schedule.

Flows:
  _flow_morning_brief  — 07:00 daily: weather + calendar + gmail + fitness in one message
  _flow_garmin_sync    — every 2h (+ once at startup): import new Garmin watch activities
                          into the workout log automatically, same as manual entry
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import DATABASE_URL, USER_TIMEZONE

logger = logging.getLogger("pa.proactive")

_tasks: list[asyncio.Task] = []
_GARMIN_SYNC_INTERVAL_SEC = 2 * 60 * 60


async def start_flows(chat_ids: list[str]) -> None:
    """Start all background proactive flows."""
    if not DATABASE_URL or not chat_ids:
        return
    _tasks.append(asyncio.create_task(_flow_morning_brief(chat_ids), name="flow_morning_brief"))
    _tasks.append(asyncio.create_task(_flow_garmin_sync(chat_ids), name="flow_garmin_sync"))
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


async def _flow_morning_brief(chat_ids: list[str]) -> None:
    """Every day at 07:00 local time: generate and send the full morning brief."""
    while True:
        tz = ZoneInfo(USER_TIMEZONE)
        now = datetime.now(tz)
        target = now.replace(hour=7, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        logger.info("Morning brief scheduled in %.0f s (at %s)", wait, target.isoformat())
        await asyncio.sleep(wait)

        for chat_id in chat_ids:
            try:
                await _send_morning_brief(chat_id)
            except Exception:
                logger.exception("Morning brief failed for chat_id=%s", chat_id)


async def _send_morning_brief(chat_id: str) -> None:
    from app.morning_brief import generate
    from app.scheduled_jobs import insert_job
    from datetime import timezone as utc

    brief = await generate(chat_id)
    insert_job(
        chat_id=chat_id,
        action_type="send_message",
        payload={"text": brief},
        run_at=datetime.now(utc.utc),
    )
    logger.info("Morning brief job created for chat_id=%s len=%d", chat_id, len(brief))


async def _flow_garmin_sync(chat_ids: list[str]) -> None:
    """Import new Garmin watch activities into the workout log automatically — the same
    log manual entries land in — instead of only syncing when the Fitness tab happens to
    be opened. Runs once immediately (to catch up), then every _GARMIN_SYNC_INTERVAL_SEC."""
    from app.garmin import client as gclient

    while True:
        try:
            if gclient.is_connected():
                from app.garmin.sync import sync_activities
                result = await sync_activities()
                if result.get("imported"):
                    await _notify_new_garmin_workouts(chat_ids, result)
        except Exception:
            logger.exception("Garmin activity sync flow failed")
        await asyncio.sleep(_GARMIN_SYNC_INTERVAL_SEC)


async def _notify_new_garmin_workouts(chat_ids: list[str], result: dict) -> None:
    """WhatsApp-confirm newly auto-imported workouts using the exact same message the
    log_workout chat tool sends — so a watch-recorded workout is documented and reads
    identically to one the user typed themselves, just tagged with a watch emoji."""
    from app.scheduled_jobs import insert_job
    from app.fitness import format_workout_log_message, list_today
    from datetime import timezone as utc

    workouts = result.get("imported_workouts") or []
    if not workouts or not chat_ids:
        return

    for chat_id in chat_ids:
        today = list_today(chat_id)
        for w in workouts:
            text = format_workout_log_message(w["parsed"], w["evaluation"], today, tag=" ⌚")
            try:
                insert_job(
                    chat_id=chat_id,
                    action_type="send_message",
                    payload={"text": text},
                    run_at=datetime.now(utc.utc),
                )
            except Exception:
                logger.exception("Failed to queue Garmin import notification for chat_id=%s", chat_id)

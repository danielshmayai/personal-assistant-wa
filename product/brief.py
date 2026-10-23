"""Morning brief for web tenants.

The WhatsApp engine sends the owner's brief at 07:00 (proactive/flows.py). Web
tenants had nothing, so this loop sends the same brief (morning_brief.generate,
run inside the tenant's own data scope) to each tenant that opted in on the
Settings page. It goes to the in-app notification feed and, shortened, as a
Web Push so it arrives with the app closed. The day's text is cached for the
Home card.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from app.config import USER_TIMEZONE

logger = logging.getLogger("product.brief")

SETTINGS_KEY = "morning_brief"         # {"enabled": bool, "time": "HH:MM"}
CACHE_KEY = "morning_brief_today"      # {"date": "YYYY-MM-DD", "text": str}
SENT_KEY = "morning_brief_last_sent"   # "YYYY-MM-DD"
DEFAULT_TIME = "07:00"
_CHECK_EVERY_S = 60

_task: asyncio.Task | None = None


def _today() -> str:
    return datetime.now(ZoneInfo(USER_TIMEZONE)).strftime("%Y-%m-%d")


def get_settings() -> dict:
    """Current tenant's brief settings (off by default)."""
    from app.memory.store import get_app_setting
    raw = get_app_setting(SETTINGS_KEY) or {}
    return {"enabled": bool(raw.get("enabled")), "time": raw.get("time") or DEFAULT_TIME}


def save_settings(enabled: bool, time: str) -> dict:
    from app.memory.store import set_app_setting
    time = datetime.strptime(time, "%H:%M").strftime("%H:%M")  # ValueError on bad input; "7:5" → "07:05"
    set_app_setting(SETTINGS_KEY, {"enabled": enabled, "time": time})
    return get_settings()


async def today_brief(chat_id: str, regenerate: bool = False) -> str:
    """Today's brief for the current tenant — cached, generated on first use."""
    from app.memory.store import get_app_setting, set_app_setting
    from app.morning_brief import generate
    from app.runtime import in_thread
    cached = await in_thread(get_app_setting, CACHE_KEY) or {}
    if not regenerate and cached.get("date") == _today() and cached.get("text"):
        return cached["text"]
    text = await generate(chat_id)
    await in_thread(set_app_setting, CACHE_KEY, {"date": _today(), "text": text})
    return text


async def _deliver(chat_id: str, text: str) -> None:
    """Notification feed + live web sessions + a short Web Push (payloads are
    capped at ~4 KB, so the push only announces it)."""
    from app.broadcast import NotificationManager
    from app.push_notifications import get_push_subscriptions, send_web_push_sync
    from app.scheduled_jobs import store_notification
    await asyncio.to_thread(store_notification, chat_id, text)
    await NotificationManager.broadcast(message=text, web_chat_id=chat_id)
    subs = await asyncio.to_thread(get_push_subscriptions, chat_id)
    await asyncio.gather(*[
        asyncio.to_thread(send_web_push_sync, sub, "🌅 בוקר טוב", "התדריך של הבוקר מוכן — הקש לפתיחה")
        for sub in subs
    ], return_exceptions=True)


async def run_for_scope(scope: str, now: datetime | None = None) -> bool:
    """Send the brief for one tenant scope if it's due. Returns True if sent."""
    from app.context import current_tenant_id
    from app.memory.store import get_app_setting, set_app_setting
    from app.runtime import in_thread
    token = current_tenant_id.set(scope)
    try:
        settings = await in_thread(get_settings)
        if not settings["enabled"]:
            return False
        now = now or datetime.now(ZoneInfo(USER_TIMEZONE))
        today = now.strftime("%Y-%m-%d")
        if now.strftime("%H:%M") < settings["time"]:
            return False
        if await in_thread(get_app_setting, SENT_KEY) == today:
            return False
        # Mark first: a failing generate must not retry every minute all day.
        await in_thread(set_app_setting, SENT_KEY, today)
        chat_id = f"web_{scope}"
        text = await today_brief(chat_id, regenerate=True)
        await _deliver(chat_id, text)
        logger.info("morning brief sent scope=%r", scope)
        return True
    except Exception:
        logger.warning("morning brief failed for scope=%r", scope, exc_info=True)
        return False
    finally:
        current_tenant_id.reset(token)


async def _loop(scopes: Callable[[], list[str]]) -> None:
    while True:
        for scope in scopes():
            await run_for_scope(scope)
        await asyncio.sleep(_CHECK_EVERY_S)


def start(scopes: Callable[[], list[str]]) -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(scopes), name="morning-brief")


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None

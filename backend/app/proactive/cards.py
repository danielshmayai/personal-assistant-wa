"""Helpers for creating typed proactive cards."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from app.scheduled_jobs import upsert_card


def trigger_card(
    chat_id: str,
    title: str,
    detail: str = "",
    action_label: str = "",
    action_chat: str = "",
    ttl_hours: float = 2.0,
) -> int:
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    return upsert_card(chat_id, "trigger", title, detail, action_label, action_chat, expires)


def notice_card(
    chat_id: str,
    title: str,
    detail: str = "",
    action_chat: str = "",
    ttl_hours: float = 24.0,
) -> int:
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    return upsert_card(chat_id, "notice", title, detail, "Tell me more", action_chat, expires)


def memory_card(
    chat_id: str,
    title: str,
    detail: str = "",
    action_chat: str = "",
    ttl_hours: float = 48.0,
) -> int:
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    return upsert_card(chat_id, "memory", title, detail, "View", action_chat, expires)

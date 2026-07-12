"""Tenant-scoped Home dashboard API for the product web app.

Mirrors backend/app/routers/dashboard.py but auth is the product session
instead of a shared TEST_TOKEN, and every call site is scoped correctly:

- get_credentials("web") is safe for every tenant as-is: google.auth._token_key
  only checks the "web" prefix and resolves the real key from current_tenant_id
  (set by require_session), so the literal suffix doesn't matter.
- scheduled_jobs.py's job/card/activity functions (list_all_jobs, cancel_job,
  get_active_cards, upsert_card, delete_card, get_activity, log_activity) all
  resolve their real DB scope internally via _scoped_chat_id() / the
  tenant_id column (P6.6) — the chat_id argument passed here (whether the
  literal "web" or _dashboard_chat_id(tenant)) is a documentation aid, not
  the source of truth; current_tenant_id is. Scheduling was owner_only in
  the registry until P6.6 (see product/modules/registry.py).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.scheduled_jobs import (
    cancel_job, delete_card, get_active_cards, list_all_jobs, upsert_card,
)
from product.deps import require_module, require_session
from product.modules.store import get_enabled_modules
from product.tenancy.models import Tenant

logger = logging.getLogger("product.dashboard")
router = APIRouter()

_jobs_gate = require_module("scheduling")
_weather_cache: dict = {}
_WEATHER_TTL = 1800


def _dashboard_chat_id(tenant: Tenant) -> str:
    """Stable per-tenant scope key for jobs/cards/activity tables.
    Owner keeps the historic 'web' key so existing rows stay visible."""
    return f"web_{tenant.engine_scope}" if tenant.engine_scope else "web"


# ── Today strip ──────────────────────────────────────────────────────────────

@router.get("/api/today")
async def get_today(tenant: Tenant = Depends(require_session)):
    from app.config import USER_CITY

    result: dict = {"weather": "", "jobs_count": 0}

    cached = _weather_cache.get(USER_CITY)
    if cached and time.time() - cached["ts"] < _WEATHER_TTL:
        result["weather"] = cached["text"]
    else:
        try:
            from app.web.tools import get_weather
            raw = await get_weather(USER_CITY)
            weather_text = ""
            for line in raw.splitlines():
                if line.startswith("**Now:**"):
                    weather_text = line.replace("**Now:**", "").strip()
                    parts = weather_text.split("  ")
                    weather_text = "  ".join(p for p in parts if "km/h" not in p).strip()
                    break
            _weather_cache[USER_CITY] = {"ts": time.time(), "text": weather_text}
            result["weather"] = weather_text
        except Exception as e:
            logger.warning("Weather fetch failed: %s", e)

    if "scheduling" in get_enabled_modules(tenant.id, tenant.is_owner):
        try:
            jobs = list_all_jobs(_dashboard_chat_id(tenant), include_done=False)
            result["jobs_count"] = len(jobs)
        except Exception:
            pass

    return result


# ── Calendar today ───────────────────────────────────────────────────────────

@router.get("/api/calendar-today")
async def get_calendar_today(
    days: int = Query(default=1, ge=1, le=30),
    _: Tenant = Depends(require_session),
):
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from app.config import USER_TIMEZONE

    tz = ZoneInfo(USER_TIMEZONE)
    now = datetime.now(tz=tz)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    range_end = today_start + timedelta(days=days)

    try:
        from app.google.auth import get_credentials
        creds = get_credentials("web")  # "web" prefix only; real scope via ContextVar
        if not creds or not creds.valid:
            return {"events": [], "connected": False}

        from googleapiclient.discovery import build
        service = build("calendar", "v3", credentials=creds)
        result = service.events().list(
            calendarId="primary",
            timeMin=today_start.astimezone(timezone.utc).isoformat(),
            timeMax=range_end.astimezone(timezone.utc).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()

        events = []
        for e in result.get("items", []):
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            full_day = "dateTime" not in e["start"]
            if full_day:
                time_str = "All day"
                date_str = start
            else:
                dt = datetime.fromisoformat(start)
                dt_local = dt.astimezone(tz)
                time_str = dt_local.strftime("%H:%M")
                date_str = dt_local.date().isoformat()
            events.append({
                "title": e.get("summary", "(no title)"),
                "time": time_str,
                "date": date_str,
                "full_day": full_day,
            })

        return {"events": events, "connected": True}
    except Exception as e:
        logger.warning("calendar-today failed: %s", e)
        return {"events": [], "connected": True, "error": str(e)}


# ── Jobs (gated by the 'scheduling' module toggle) ──────────────────────────

@router.get("/api/jobs")
async def list_jobs(include_done: bool = Query(False), tenant: Tenant = Depends(_jobs_gate)):
    return {"jobs": list_all_jobs(_dashboard_chat_id(tenant), include_done=include_done)}


@router.delete("/api/jobs/{job_id}")
async def cancel_job_endpoint(job_id: int, tenant: Tenant = Depends(_jobs_gate)):
    if not cancel_job(job_id, _dashboard_chat_id(tenant)):
        raise HTTPException(status_code=404, detail="job_not_found")
    return {"ok": True}


# ── Activity log ─────────────────────────────────────────────────────────────

class ActivityEntry(BaseModel):
    event_type: str
    summary: str
    detail: dict | None = None


@router.get("/api/activity")
async def get_activity_log(
    tenant: Tenant = Depends(require_session),
    limit: int = Query(50, le=200),
    event_type: str | None = Query(None),
):
    from app.scheduled_jobs import get_activity
    return {"entries": get_activity(_dashboard_chat_id(tenant), limit=limit, event_type=event_type)}


@router.post("/api/activity")
async def post_activity(entry: ActivityEntry, tenant: Tenant = Depends(require_session)):
    from app.scheduled_jobs import log_activity
    log_activity(_dashboard_chat_id(tenant), entry.event_type, entry.summary, entry.detail)
    return {"ok": True}


# ── Proactive cards (open to every tenant; empty until P6+ per-tenant flows) ─

class CardCreate(BaseModel):
    card_type: str
    title: str
    detail: str = ""
    action_label: str = ""
    action_chat: str = ""
    expires_at: str | None = None


@router.get("/api/proactive-cards")
async def get_cards(tenant: Tenant = Depends(require_session)):
    return {"cards": get_active_cards(_dashboard_chat_id(tenant))}


@router.post("/api/proactive-cards")
async def create_card(card: CardCreate, tenant: Tenant = Depends(require_session)):
    expires = None
    if card.expires_at:
        try:
            expires = datetime.fromisoformat(card.expires_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_expires_at")
    card_id = upsert_card(
        chat_id=_dashboard_chat_id(tenant),
        card_type=card.card_type,
        title=card.title,
        detail=card.detail,
        action_label=card.action_label,
        action_chat=card.action_chat,
        expires_at=expires,
    )
    return {"id": card_id}


@router.delete("/api/proactive-cards/{card_id}")
async def dismiss_card(card_id: int, tenant: Tenant = Depends(require_session)):
    if not delete_card(card_id, _dashboard_chat_id(tenant)):
        raise HTTPException(status_code=404, detail="card_not_found")
    return {"ok": True}

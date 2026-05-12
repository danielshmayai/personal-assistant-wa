"""Dashboard API — scheduled jobs, activity log, proactive cards."""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from datetime import datetime, timezone
import time

from app.config import TEST_TOKEN
from app.scheduled_jobs import (
    list_all_jobs, cancel_job,
    get_activity, log_activity,
    get_active_cards, upsert_card, delete_card,
)

logger = logging.getLogger("pa.dashboard")
router = APIRouter()
_bearer = HTTPBearer(auto_error=False)

# Simple in-process weather cache (ttl = 30 min)
_weather_cache: dict = {}
_WEATHER_TTL = 1800


def _require_token(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if not TEST_TOKEN or not creds or creds.credentials != TEST_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return creds.credentials


# ── Jobs ──────────────────────────────────────────────────────────────────────

@router.get("/api/jobs")
async def list_jobs(
    chat_id: str = Query(...),
    include_done: bool = Query(False),
    _: str = Depends(_require_token),
):
    jobs = list_all_jobs(chat_id, include_done=include_done)
    return {"jobs": jobs}


@router.delete("/api/jobs/{job_id}")
async def cancel_job_endpoint(
    job_id: int,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    ok = cancel_job(job_id, chat_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found or already completed")
    return {"ok": True}


# ── Activity ──────────────────────────────────────────────────────────────────

class ActivityEntry(BaseModel):
    event_type: str
    summary: str
    detail: dict | None = None


@router.get("/api/activity")
async def get_activity_log(
    chat_id: str = Query(...),
    limit: int = Query(50, le=200),
    event_type: str | None = Query(None),
    _: str = Depends(_require_token),
):
    entries = get_activity(chat_id, limit=limit, event_type=event_type)
    return {"entries": entries}


@router.post("/api/activity")
async def post_activity(
    entry: ActivityEntry,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    log_activity(chat_id, entry.event_type, entry.summary, entry.detail)
    return {"ok": True}


# ── Proactive cards ───────────────────────────────────────────────────────────

class CardCreate(BaseModel):
    card_type: str  # "trigger" | "notice" | "memory"
    title: str
    detail: str = ""
    action_label: str = ""
    action_chat: str = ""
    expires_at: str | None = None  # ISO datetime string


@router.get("/api/proactive-cards")
async def get_cards(
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    cards = get_active_cards(chat_id)
    return {"cards": cards}


@router.post("/api/proactive-cards")
async def create_card(
    card: CardCreate,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    expires = None
    if card.expires_at:
        try:
            expires = datetime.fromisoformat(card.expires_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid expires_at format")
    card_id = upsert_card(
        chat_id=chat_id,
        card_type=card.card_type,
        title=card.title,
        detail=card.detail,
        action_label=card.action_label,
        action_chat=card.action_chat,
        expires_at=expires,
    )
    return {"id": card_id}


@router.delete("/api/proactive-cards/{card_id}")
async def dismiss_card(
    card_id: int,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    ok = delete_card(card_id, chat_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Card not found")
    return {"ok": True}


# ── Today strip ───────────────────────────────────────────────────────────────

@router.get("/api/today")
async def get_today(
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    """Return today data for the Home strip: weather + pending jobs count."""
    from app.config import USER_CITY
    from app.scheduled_jobs import list_all_jobs

    result: dict = {"weather": "", "jobs_count": 0}

    # ── Weather (cached 30 min) ──────────────────────────────────────────────
    cached = _weather_cache.get(USER_CITY)
    if cached and time.time() - cached["ts"] < _WEATHER_TTL:
        result["weather"] = cached["text"]
    else:
        try:
            from app.web.tools import get_weather
            raw = await get_weather(USER_CITY)
            # Extract the "Now:" line: "**Now:** ☀️ Clear  25.3°C  💨 10 km/h"
            weather_text = ""
            for line in raw.splitlines():
                if line.startswith("**Now:**"):
                    weather_text = line.replace("**Now:**", "").strip()
                    # Keep only emoji+desc and temperature (drop wind)
                    parts = weather_text.split("  ")
                    weather_text = "  ".join(p for p in parts if "km/h" not in p).strip()
                    break
            _weather_cache[USER_CITY] = {"ts": time.time(), "text": weather_text}
            result["weather"] = weather_text
        except Exception as e:
            logger.warning("Weather fetch failed: %s", e)

    # ── Jobs count ───────────────────────────────────────────────────────────
    try:
        jobs = list_all_jobs(chat_id, include_done=False)
        result["jobs_count"] = len(jobs)
    except Exception:
        pass

    return result

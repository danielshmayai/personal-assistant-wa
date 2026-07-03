"""Garmin API — connect/MFA, wellness autosync, activity import, workout push."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import TEST_TOKEN
from app.garmin import client as gclient
from app.garmin import sync as gsync
from app.garmin import workout_push

logger = logging.getLogger("pa.garmin")
router = APIRouter()
_bearer = HTTPBearer(auto_error=False)


def _require_token(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if not TEST_TOKEN or not creds or creds.credentials != TEST_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return creds.credentials


class ConnectRequest(BaseModel):
    email: str
    password: str


class MfaRequest(BaseModel):
    code: str


class PushWorkoutRequest(BaseModel):
    plan: dict


@router.get("/api/garmin/status")
async def status(_: str = Depends(_require_token)):
    connected = await asyncio.to_thread(gclient.is_connected)
    today = None
    if connected:
        try:
            from app.fitness import _user_today
            today = await asyncio.to_thread(gsync.get_wellness, _user_today())
        except Exception:
            logger.debug("garmin status wellness read failed", exc_info=True)
    return {"connected": connected, "today": today}


@router.post("/api/garmin/connect")
async def connect(body: ConnectRequest, _: str = Depends(_require_token)):
    email = body.email.strip()
    if not email or not body.password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    try:
        result = await asyncio.to_thread(gclient.begin_login, email, body.password)
    except Exception as exc:
        logger.warning("garmin connect failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Garmin login failed: {exc}")
    return result


@router.post("/api/garmin/mfa")
async def mfa(body: MfaRequest, _: str = Depends(_require_token)):
    if not body.code.strip():
        raise HTTPException(status_code=400, detail="MFA code is required")
    try:
        result = await asyncio.to_thread(gclient.finish_mfa, body.code)
    except gclient.GarminNotConnected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.warning("garmin mfa failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Garmin MFA failed: {exc}")
    return result


@router.post("/api/garmin/disconnect")
async def disconnect(_: str = Depends(_require_token)):
    await asyncio.to_thread(gclient.disconnect)
    return {"connected": False}


@router.get("/api/garmin/wellness")
async def wellness(force: bool = False, _: str = Depends(_require_token)):
    """Today's wellness snapshot; auto-refreshes when stale and kicks off activity import."""
    if not await asyncio.to_thread(gclient.is_connected):
        return {"connected": False, "today": None}
    try:
        today = await gsync.sync_wellness(force=force)
    except gclient.GarminNotConnected:
        return {"connected": False, "today": None}
    except Exception as exc:
        logger.warning("garmin wellness sync failed: %s", exc)
        try:
            from app.fitness import _user_today
            today = await asyncio.to_thread(gsync.get_wellness, _user_today())
        except Exception:
            today = None
    # Activity import runs in the background — the card never waits for it.
    asyncio.create_task(_safe_sync_activities())
    return {"connected": True, "today": today}


async def _safe_sync_activities():
    try:
        await gsync.sync_activities()
    except Exception:
        logger.debug("background garmin activity sync failed", exc_info=True)


@router.post("/api/garmin/push-workout")
async def push_workout_route(body: PushWorkoutRequest, _: str = Depends(_require_token)):
    if not await asyncio.to_thread(gclient.is_connected):
        raise HTTPException(status_code=409, detail="Garmin is not connected")
    try:
        result = await workout_push.push_workout(body.plan)
    except gclient.GarminNotConnected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.warning("garmin push-workout failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Could not push workout to Garmin: {exc}")
    return result

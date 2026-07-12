"""Tenant-scoped Garmin Connect API for the product web app.

Mirrors the old owner-only router (backend/app/routers/garmin.py) but reads
credentials from the tenant's own secrets catalog entry (GARMIN_EMAIL/
GARMIN_PASSWORD) instead of a request body, and everything underneath —
garmin_tokens, garmin_wellness, garmin.client's in-memory client/MFA caches —
is tenant-keyed (P2/P4 hardening), so one tenant's connection can never
resume, overwrite, or read another tenant's (or the owner's) Garmin session.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import runtime
from app.garmin import client as gclient
from app.garmin import sync as gsync
from product.deps import require_module
from product.tenancy.models import Tenant

logger = logging.getLogger("product.garmin")

router = APIRouter(prefix="/api/garmin")
_gate = require_module("fitness")


class MfaRequest(BaseModel):
    code: str


class PushWorkoutRequest(BaseModel):
    plan: dict


@router.get("/status")
async def status(_: Tenant = Depends(_gate)):
    connected = await asyncio.to_thread(gclient.is_connected)
    today = None
    if connected:
        try:
            from app.fitness import _user_today
            today = await asyncio.to_thread(gsync.get_wellness, _user_today())
        except Exception:
            logger.debug("garmin status wellness read failed", exc_info=True)
    return {"connected": connected, "today": today}


@router.post("/connect")
async def connect(_: Tenant = Depends(_gate)):
    email = runtime.get_secret("GARMIN_EMAIL").strip()
    password = runtime.get_secret("GARMIN_PASSWORD")
    if not email or not password:
        raise HTTPException(
            status_code=400,
            detail="Add your Garmin Connect email and password under Settings → Secrets first",
        )
    try:
        result = await asyncio.to_thread(gclient.begin_login, email, password)
    except Exception as exc:
        logger.warning("tenant garmin connect failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Garmin login failed: {exc}")
    return result


@router.post("/mfa")
async def mfa(body: MfaRequest, _: Tenant = Depends(_gate)):
    if not body.code.strip():
        raise HTTPException(status_code=400, detail="MFA code is required")
    try:
        result = await asyncio.to_thread(gclient.finish_mfa, body.code)
    except gclient.GarminNotConnected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.warning("tenant garmin mfa failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Garmin MFA failed: {exc}")
    return result


@router.post("/disconnect")
async def disconnect(_: Tenant = Depends(_gate)):
    await asyncio.to_thread(gclient.disconnect)
    return {"connected": False}


@router.get("/wellness")
async def wellness(force: bool = False, _: Tenant = Depends(_gate)):
    """Today's wellness snapshot; auto-refreshes when stale and kicks off activity import.

    asyncio.create_task copies the current contextvars context (unlike
    loop.run_in_executor), so current_tenant_id propagates correctly into
    the background activity-sync task below — it stays scoped to this tenant.
    """
    if not await asyncio.to_thread(gclient.is_connected):
        return {"connected": False, "today": None}
    try:
        today = await gsync.sync_wellness(force=force)
    except gclient.GarminNotConnected:
        return {"connected": False, "today": None}
    except Exception as exc:
        logger.warning("tenant garmin wellness sync failed: %s", exc)
        try:
            from app.fitness import _user_today
            today = await asyncio.to_thread(gsync.get_wellness, _user_today())
        except Exception:
            today = None
    asyncio.create_task(_safe_sync_activities())
    return {"connected": True, "today": today}


async def _safe_sync_activities():
    try:
        await gsync.sync_activities()
    except Exception:
        logger.debug("background tenant garmin activity sync failed", exc_info=True)


@router.post("/push-workout")
async def push_workout_route(body: PushWorkoutRequest, _: Tenant = Depends(_gate)):
    from app.garmin import workout_push

    if not await asyncio.to_thread(gclient.is_connected):
        raise HTTPException(status_code=409, detail="Garmin is not connected")
    try:
        result = await workout_push.push_workout(body.plan)
    except gclient.GarminNotConnected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.warning("tenant garmin push-workout failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Could not push workout to Garmin: {exc}")
    return result

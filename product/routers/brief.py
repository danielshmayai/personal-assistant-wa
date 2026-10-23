"""Morning-brief settings and today's brief (Home card) for the web app."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from product import brief
from product.deps import require_session
from product.tenancy.models import Tenant

router = APIRouter(prefix="/api/brief")


def _chat_id(tenant: Tenant) -> str:
    return f"web_{tenant.engine_scope}" if tenant.engine_scope else "web"


@router.get("/settings")
async def get_brief_settings(_: Tenant = Depends(require_session)):
    return await asyncio.to_thread(brief.get_settings)


class BriefSettings(BaseModel):
    enabled: bool
    time: str = brief.DEFAULT_TIME


@router.put("/settings")
async def put_brief_settings(body: BriefSettings, _: Tenant = Depends(require_session)):
    try:
        return await asyncio.to_thread(brief.save_settings, body.enabled, body.time)
    except ValueError:
        raise HTTPException(status_code=422, detail="time must be HH:MM")


@router.get("/today")
async def get_today_brief(refresh: bool = False, tenant: Tenant = Depends(require_session)):
    return {"text": await brief.today_brief(_chat_id(tenant), regenerate=refresh)}

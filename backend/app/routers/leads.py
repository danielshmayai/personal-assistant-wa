"""Leads API — manage watched WhatsApp contacts for silent info gathering."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import TEST_TOKEN
import app.leads as leads_db

logger = logging.getLogger("pa.leads_router")
router = APIRouter(prefix="/api/leads", tags=["leads"])


def _require_token(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
) -> str:
    if not TEST_TOKEN or credentials.credentials != TEST_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials.credentials


class AddLeadRequest(BaseModel):
    phone: str
    label: str = ""
    obsidian_note: str = ""


def _serialize(lead: dict) -> dict:
    for k in ("created_at", "last_activity"):
        if lead.get(k) is not None:
            lead[k] = lead[k].isoformat()
    return lead


@router.get("")
async def list_leads(_: str = Depends(_require_token)):
    loop = asyncio.get_event_loop()
    items = await loop.run_in_executor(None, leads_db.get_all_leads)
    return [_serialize(item) for item in items]


@router.post("")
async def add_lead(req: AddLeadRequest, _: str = Depends(_require_token)):
    if not req.phone.strip():
        raise HTTPException(status_code=400, detail="phone is required")
    loop = asyncio.get_event_loop()
    lead = await loop.run_in_executor(
        None, leads_db.add_lead, req.phone.strip(), req.label, req.obsidian_note
    )
    leads_db.invalidate_cache()
    return _serialize(lead)


@router.delete("/{lead_id}")
async def remove_lead(lead_id: int, _: str = Depends(_require_token)):
    loop = asyncio.get_event_loop()
    deleted = await loop.run_in_executor(None, leads_db.delete_lead, lead_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Lead not found")
    leads_db.invalidate_cache()
    return {"ok": True}

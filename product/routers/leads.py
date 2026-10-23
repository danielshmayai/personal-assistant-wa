"""Watched WhatsApp contacts ("leads") — owner only.

Leads come from the owner's WhatsApp engine (app.leads), whose data lives in
the legacy owner scope, so this is available only to the owner tenant whose
engine scope is that one (the local install). Moved here from the retired
engine router (backend/app/routers/leads.py).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import app.leads as leads_db
from product.deps import require_session
from product.tenancy.models import Tenant

router = APIRouter(prefix="/api/leads")


def _owner_engine(tenant: Tenant = Depends(require_session)) -> Tenant:
    if not (tenant.is_owner and not tenant.engine_scope):
        raise HTTPException(status_code=404, detail="not_found")
    return tenant


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
async def list_leads(_: Tenant = Depends(_owner_engine)):
    return {"leads": [_serialize(x) for x in await asyncio.to_thread(leads_db.get_all_leads)]}


@router.post("")
async def add_lead(req: AddLeadRequest, _: Tenant = Depends(_owner_engine)):
    if not req.phone.strip():
        raise HTTPException(status_code=400, detail="phone is required")
    lead = await asyncio.to_thread(leads_db.add_lead, req.phone.strip(), req.label, req.obsidian_note)
    leads_db.invalidate_cache()
    return _serialize(lead)


@router.delete("/{lead_id}")
async def remove_lead(lead_id: int, _: Tenant = Depends(_owner_engine)):
    if not await asyncio.to_thread(leads_db.delete_lead, lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")
    leads_db.invalidate_cache()
    return {"ok": True}

"""Tenant-scoped Web Push subscription API for the product web app.

VAPID keys identify the *server* (one shared identity is correct — this is
standard Web Push architecture, not a privacy concern). Subscriptions
themselves are stored per chat_id with no ContextVar awareness (same shape
as scheduled_jobs.py, see product/routers/dashboard.py's _dashboard_chat_id)
so we build a per-tenant key at the call site rather than passing a shared
literal.

Note: nothing in the product process currently *sends* a push — delivery
only fires from scheduled_jobs.py's job loop, which only runs in the
owner's engine entrypoint (see PLANNING.md P6.5/P6.6). Subscribing here is
still safe and forward-compatible: it correctly records the tenant's own
endpoint so delivery works immediately once that scheduler gap closes.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from product.deps import require_session
from product.tenancy.models import Tenant

router = APIRouter(prefix="/api/push")


def _push_chat_id(tenant: Tenant) -> str:
    return f"web_{tenant.engine_scope}" if tenant.engine_scope else "web"


@router.get("/vapid-public-key")
async def vapid_public_key(_: Tenant = Depends(require_session)):
    from app.push_notifications import get_vapid_public_key
    return {"key": get_vapid_public_key()}


class SubscribeBody(BaseModel):
    subscription: dict


@router.post("/subscribe")
async def subscribe_push(body: SubscribeBody, tenant: Tenant = Depends(require_session)):
    from app.push_notifications import save_push_subscription
    await asyncio.to_thread(save_push_subscription, _push_chat_id(tenant), body.subscription)
    return {"ok": True}

"""Tenant-scoped Web Push subscription API for the product web app.

VAPID keys identify the *server* (one shared identity is correct — this is
standard Web Push architecture, not a privacy concern). Subscriptions
themselves are stored per chat_id with no ContextVar awareness (same shape
as scheduled_jobs.py, see product/routers/dashboard.py's _dashboard_chat_id)
so we build a per-tenant key at the call site rather than passing a shared
literal.

Delivery: the product runs a tenant-scoped scheduler (product/main.py), whose
_notify() pushes every reminder for "web_<scope>" to these subscriptions, and
the per-tenant morning brief is delivered the same way.
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


class UnsubscribeBody(BaseModel):
    endpoint: str


@router.post("/unsubscribe")
async def unsubscribe_push(body: UnsubscribeBody, tenant: Tenant = Depends(require_session)):
    """Remove this browser's subscription — only if it belongs to this tenant."""
    from app.push_notifications import delete_push_subscription, get_push_subscriptions
    subs = await asyncio.to_thread(get_push_subscriptions, _push_chat_id(tenant))
    if any(s.get("endpoint") == body.endpoint for s in subs):
        await asyncio.to_thread(delete_push_subscription, body.endpoint)
    return {"ok": True}


@router.post("/test")
async def test_push(tenant: Tenant = Depends(require_session)):
    """Send a test notification to every device this tenant subscribed."""
    from app.push_notifications import get_push_subscriptions, send_web_push_sync
    subs = await asyncio.to_thread(get_push_subscriptions, _push_chat_id(tenant))
    await asyncio.gather(*[
        asyncio.to_thread(send_web_push_sync, sub, "danidin 🔔", "Notifications are working.")
        for sub in subs
    ], return_exceptions=True)
    return {"sent": len(subs)}

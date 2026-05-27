"""Web Push Notification endpoints."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import TEST_TOKEN

router = APIRouter()

_bearer = HTTPBearer(auto_error=False)


def _require_token(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if not TEST_TOKEN or not creds or creds.credentials != TEST_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    return creds.credentials


@router.get("/api/push/vapid-public-key")
async def vapid_public_key(_: str = Depends(_require_token)):
    """Return the VAPID public key for the browser to subscribe with."""
    from app.push_notifications import get_vapid_public_key
    return {"key": get_vapid_public_key()}


class _SubscribeBody(BaseModel):
    chat_id: str
    subscription: dict


@router.post("/api/push/subscribe")
async def subscribe_push(body: _SubscribeBody, _: str = Depends(_require_token)):
    """Store a browser push subscription for the given chat_id."""
    from app.push_notifications import save_push_subscription
    await asyncio.to_thread(save_push_subscription, body.chat_id, body.subscription)
    return {"ok": True}

"""Gmail/Calendar/Drive consent flow (incremental scopes, separate from login).

Reuses the engine's OAuth machinery (app/google/auth.py). The engine keys
token rows via the tenant ContextVar, so this router sets it both when the
flow starts and — because Google's redirect carries no session — again in
the callback, recovered from the oauth state row's chat_id ("web_<tenant>").
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.context import current_tenant_id
from product.config import PRODUCT_BASE_URL
from product.deps import require_session
from product.tenancy.models import Tenant
from product.tenancy.store import get_tenant

logger = logging.getLogger("product.google")

router = APIRouter()

_CONNECT_PREFIX = "web_"


@router.get("/api/google/connect")
async def google_connect(tenant: Tenant = Depends(require_session)):
    from app.google.auth import get_auth_url
    # chat_id encodes the engine scope so the callback can recover it.
    url = get_auth_url(f"{_CONNECT_PREFIX}{tenant.engine_scope}")
    return {"auth_url": url}


@router.get("/auth/google/callback")
async def google_callback(code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse(f"{PRODUCT_BASE_URL}/settings?google=denied", status_code=302)

    from app.google.auth import handle_callback
    from app.memory.store import peek_oauth_state

    chat_id = peek_oauth_state(state)
    if not chat_id or not chat_id.startswith(_CONNECT_PREFIX):
        raise HTTPException(status_code=400, detail="unknown_or_expired_state")

    engine_scope = chat_id[len(_CONNECT_PREFIX):]
    if engine_scope:
        # Non-owner: the tenant must still exist and be active.
        t = get_tenant(engine_scope)
        if t is None or t.status != "active":
            raise HTTPException(status_code=403, detail="tenant_not_active")
    current_tenant_id.set(engine_scope)

    try:
        handle_callback(code, state)
    except Exception:
        logger.exception("Google consent callback failed")
        return RedirectResponse(f"{PRODUCT_BASE_URL}/settings?google=failed", status_code=302)
    return RedirectResponse(f"{PRODUCT_BASE_URL}/settings?google=connected", status_code=302)


@router.delete("/api/google/connect")
async def google_disconnect(tenant: Tenant = Depends(require_session)):
    from app.google.auth import _token_key
    from app.memory.store import delete_google_token
    delete_google_token(_token_key("web_probe"))
    return {"ok": True}

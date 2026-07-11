"""Login (Google OIDC), callback, logout."""
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from product.auth import oidc
from product.auth.sessions import COOKIE_NAME, issue_token, verify_token
from product.config import ENVIRONMENT, PRODUCT_BASE_URL, SESSION_TTL_DAYS
from product.modules.store import _ensure_defaults
from product.tenancy.models import Tenant
from product.tenancy.store import create_session, revoke_session, upsert_tenant_from_google

logger = logging.getLogger("product.auth")

router = APIRouter()

_COOKIE_SECURE = ENVIRONMENT == "production" or PRODUCT_BASE_URL.startswith("https://")


async def _notify_owner_of_request(tenant: Tenant) -> None:
    """Ping the owner on WhatsApp so they can approve the new account by reply.

    Best-effort: a WAHA/network failure must never break the login flow.
    Reaches the owner's WhatsApp stack over the shared pa-net network.
    """
    from app.config import MY_WHATSAPP_ID
    if not MY_WHATSAPP_ID:
        return
    from app.whatsapp import send_whatsapp_message
    name = tenant.display_name or tenant.email
    msg = (
        "‏[ *danidin* ] 🔔 New access request\n"
        f"{name}\n{tenant.email}\n\n"
        f"Reply:  approve {tenant.email}\n"
        f"   or:  deny {tenant.email}"
    )
    try:
        await send_whatsapp_message(MY_WHATSAPP_ID, msg)
    except Exception:
        logger.exception("Failed to notify owner of access request for %s", tenant.email)


@router.get("/auth/login")
async def login():
    return RedirectResponse(oidc.build_login_url(), status_code=302)


@router.get("/auth/callback")
async def callback(code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse(f"{PRODUCT_BASE_URL}/login?error=google_denied", status_code=302)
    if not oidc.consume_login_state(state):
        return RedirectResponse(f"{PRODUCT_BASE_URL}/login?error=state_expired", status_code=302)

    try:
        claims = await oidc.exchange_code(code)
    except Exception:
        logger.exception("OIDC code exchange failed")
        return RedirectResponse(f"{PRODUCT_BASE_URL}/login?error=exchange_failed", status_code=302)

    tenant, created = upsert_tenant_from_google(
        sub=claims["sub"],
        email=claims.get("email", ""),
        name=claims.get("name", ""),
        picture=claims.get("picture", ""),
    )
    if tenant.status == "pending":
        # New account awaiting owner approval — no session issued.
        if created:
            await _notify_owner_of_request(tenant)
        return RedirectResponse(f"{PRODUCT_BASE_URL}/login?error=account_pending", status_code=302)
    if tenant.status != "active":
        return RedirectResponse(f"{PRODUCT_BASE_URL}/login?error=account_disabled", status_code=302)

    _ensure_defaults(tenant.id)
    jti, expires = create_session(tenant.id)
    token = issue_token(jti, tenant.id, int(expires.timestamp()))

    dest = "/" if tenant.onboarded_at else "/onboarding"
    resp = RedirectResponse(f"{PRODUCT_BASE_URL}{dest}", status_code=302)
    resp.set_cookie(
        COOKIE_NAME, token,
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True, secure=_COOKIE_SECURE, samesite="lax", path="/",
    )
    return resp


@router.post("/auth/logout")
async def logout(request: Request):
    token = request.cookies.get(COOKIE_NAME, "")
    payload = verify_token(token)
    if payload:
        revoke_session(payload["jti"])
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp

"""FastAPI dependencies: session auth, CSRF guard, per-tenant rate limits."""
import logging
import time

from fastapi import Depends, HTTPException, Request

from app.context import current_tenant_id
from product.auth.sessions import COOKIE_NAME, verify_token
from product.config import API_REQUESTS_PER_MINUTE, CHAT_MESSAGES_PER_MINUTE
from product.tenancy.models import Tenant
from product.tenancy.store import get_session_tenant

logger = logging.getLogger("product.deps")

CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "pa-app"
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def resolve_session_tenant(token: str | None) -> Tenant | None:
    """Cookie token → live session row → active tenant (None otherwise)."""
    if not token:
        return None
    payload = verify_token(token)
    if not payload:
        return None
    # DB is the source of truth: revocation and tenant status are enforced
    # on every request, not just at login.
    return get_session_tenant(payload["jti"])


async def require_session(request: Request) -> Tenant:
    tenant = resolve_session_tenant(request.cookies.get(COOKIE_NAME))
    if tenant is None:
        raise HTTPException(status_code=401, detail="not_authenticated")
    if request.method in _MUTATING and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status_code=403, detail="csrf_header_missing")
    # Engine data scope for everything this request touches.
    current_tenant_id.set(tenant.engine_scope)
    _rate_limit(tenant.id, "api", API_REQUESTS_PER_MINUTE)
    return tenant


async def require_owner(tenant: Tenant = Depends(require_session)) -> Tenant:
    if not tenant.is_owner:
        raise HTTPException(status_code=403, detail="owner_only")
    return tenant


def require_module(module_id: str):
    """Dependency factory: require a live session AND that the tenant has the
    given module enabled. Composes require_session, so the tenant ContextVar,
    CSRF guard and per-tenant rate limit all still apply before any data
    function runs. Returns 403 module_disabled otherwise.
    """
    from product.modules.store import get_enabled_modules

    async def _dep(tenant: Tenant = Depends(require_session)) -> Tenant:
        if module_id not in get_enabled_modules(tenant.id, tenant.is_owner):
            raise HTTPException(status_code=403, detail="module_disabled")
        return tenant

    return _dep


# ── Per-tenant token buckets (in-process; fine for single instance) ────────

_buckets: dict[tuple[str, str], tuple[float, float]] = {}  # (tenant, scope) → (tokens, last_ts)


def _rate_limit(tenant_id: str, scope: str, per_minute: int) -> None:
    if not check_rate(tenant_id, scope, per_minute):
        raise HTTPException(status_code=429, detail="rate_limited")


def check_rate(tenant_id: str, scope: str, per_minute: int) -> bool:
    """Token bucket: True if the call is allowed."""
    now = time.monotonic()
    key = (tenant_id, scope)
    tokens, last = _buckets.get(key, (float(per_minute), now))
    tokens = min(float(per_minute), tokens + (now - last) * per_minute / 60.0)
    if tokens < 1.0:
        _buckets[key] = (tokens, now)
        return False
    _buckets[key] = (tokens - 1.0, now)
    return True


def check_chat_rate(tenant_id: str) -> bool:
    return check_rate(tenant_id, "chat", CHAT_MESSAGES_PER_MINUTE)

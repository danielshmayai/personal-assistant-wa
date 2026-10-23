"""Owner-only tenant administration and the Assistant-health numbers."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException

from product.deps import require_owner
from product.offboarding import delete_tenant_data
from product.tenancy.models import Tenant
from product.tenancy.store import get_tenant, list_tenants, revoke_all_sessions, set_tenant_status

router = APIRouter(prefix="/api/admin")


@router.get("/tenants")
async def tenants(_: Tenant = Depends(require_owner)):
    return {
        "tenants": [
            {
                "id": t.id, "email": t.email, "display_name": t.display_name,
                "is_owner": t.is_owner, "status": t.status,
                "onboarded": t.onboarded_at is not None,
            }
            for t in list_tenants()
        ]
    }


def _target(tenant_id: str) -> Tenant:
    target = get_tenant(tenant_id)
    if target is None:
        raise HTTPException(status_code=404, detail="tenant_not_found")
    if target.is_owner:
        raise HTTPException(status_code=400, detail="cannot_modify_owner")
    return target


@router.post("/tenants/{tenant_id}/approve")
async def approve(tenant_id: str, _: Tenant = Depends(require_owner)):
    target = _target(tenant_id)
    if target.status != "pending":
        raise HTTPException(status_code=400, detail="not_pending")
    set_tenant_status(target.id, "active")
    return {"ok": True}


@router.post("/tenants/{tenant_id}/revoke")
async def revoke(tenant_id: str, _: Tenant = Depends(require_owner)):
    target = _target(tenant_id)
    set_tenant_status(target.id, "revoked")
    revoke_all_sessions(target.id)
    return {"ok": True}


@router.post("/tenants/{tenant_id}/restore")
async def restore(tenant_id: str, _: Tenant = Depends(require_owner)):
    target = _target(tenant_id)
    if target.status == "deleted":
        raise HTTPException(status_code=400, detail="tenant_deleted")
    set_tenant_status(target.id, "active")
    return {"ok": True}


@router.delete("/tenants/{tenant_id}")
async def delete(tenant_id: str, _: Tenant = Depends(require_owner)):
    target = _target(tenant_id)
    await delete_tenant_data(target.id, target.engine_scope)
    return {"ok": True}


@router.get("/health")
async def assistant_health(owner: Tenant = Depends(require_owner)):
    """Is the assistant actually helping? Memory writes and tool use come from
    the database (last 7 days); latency and retrieval hit-rate are counted by
    this web server since its last restart (WhatsApp runs in its own process)."""
    from app import metrics
    from app.memory import embeddings
    from app.memory.store import memory_event_counts
    from app.scheduled_jobs import activity_counts

    chat_ids = [f"web_{owner.engine_scope}" if owner.engine_scope else "web"]
    if not owner.engine_scope:
        from app.config import MY_WHATSAPP_ID
        if MY_WHATSAPP_ID:
            chat_ids.append(MY_WHATSAPP_ID)

    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception:
            return {}

    memory, tools = await asyncio.gather(
        asyncio.to_thread(_safe, memory_event_counts, 7),
        asyncio.to_thread(_safe, activity_counts, chat_ids, 7),
    )
    emb = embeddings.status()
    return {
        "memory_writes_7d": memory,
        "tool_calls_7d": tools,
        "embeddings": {k: emb.get(k) for k in ("state", "model", "last_error", "last_error_at", "errors")},
        "web": metrics.snapshot(),
    }

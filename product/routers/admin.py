"""Owner-only tenant administration."""
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

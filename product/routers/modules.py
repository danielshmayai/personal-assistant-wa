"""Module toggle API."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from product.deps import require_session
from product.modules.store import get_enabled_modules, set_module
from product.tenancy.models import Tenant

router = APIRouter(prefix="/api/modules")


class ModuleToggle(BaseModel):
    module_id: str
    enabled: bool


@router.get("")
async def modules(tenant: Tenant = Depends(require_session)):
    return {"enabled": get_enabled_modules(tenant.id, tenant.is_owner)}


@router.put("")
async def toggle(body: ModuleToggle, tenant: Tenant = Depends(require_session)):
    try:
        set_module(tenant.id, body.module_id, body.enabled, tenant.is_owner)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"enabled": get_enabled_modules(tenant.id, tenant.is_owner)}

"""Onboarding wizard: required Gemini key, optional keys, module selection."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import runtime
from product.deps import require_session
from product.modules.store import set_modules_bulk
from product.secrets import get_secrets_backend
from product.secrets.catalog import ALLOWED_KEYS, validate_secret
from product.tenancy.models import Tenant
from product.tenancy.store import mark_onboarded

router = APIRouter(prefix="/api")


class OnboardingPayload(BaseModel):
    gemini_api_key: str
    secrets: dict[str, str] = {}
    modules: list[str] = []


@router.post("/onboarding")
async def complete_onboarding(body: OnboardingPayload, tenant: Tenant = Depends(require_session)):
    gemini_key = body.gemini_api_key.strip()
    if not gemini_key:
        raise HTTPException(status_code=400, detail="gemini_key_required")
    ok, message = await validate_secret("GEMINI_API_KEY", gemini_key)
    if not ok:
        raise HTTPException(status_code=422, detail=message)

    backend = get_secrets_backend()
    backend.set(tenant.id, "GEMINI_API_KEY", gemini_key)
    for key, value in body.secrets.items():
        if key in ALLOWED_KEYS and value.strip():
            backend.set(tenant.id, key, value.strip())

    if body.modules:
        set_modules_bulk(tenant.id, body.modules, tenant.is_owner)

    mark_onboarded(tenant.id)
    runtime.on_secrets_changed(tenant.engine_scope)
    return {"ok": True}

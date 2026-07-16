"""Tenant secret management: masked reads, validated writes, cache busting."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import runtime
from product.deps import require_session
from product.secrets import get_secrets_backend
from product.secrets.catalog import ALLOWED_KEYS, SECRET_SPECS, resolve_gemini_access, validate_secret
from product.tenancy.models import Tenant

logger = logging.getLogger("product.secrets.api")

router = APIRouter(prefix="/api/secrets")


class SecretWrite(BaseModel):
    key: str
    value: str


@router.get("")
async def list_secrets(tenant: Tenant = Depends(require_session)):
    present = get_secrets_backend().list_present(tenant.id)
    return {
        s.key: {"set": s.key in present, "label": s.label, "module": s.module,
                "required": s.required, "help_url": s.help_url}
        for s in SECRET_SPECS
    }


@router.put("")
async def put_secret(body: SecretWrite, tenant: Tenant = Depends(require_session)):
    if body.key not in ALLOWED_KEYS:
        raise HTTPException(status_code=400, detail="unknown_secret_key")
    value = body.value.strip()
    if not value:
        raise HTTPException(status_code=400, detail="empty_value")

    backend = get_secrets_backend()

    if body.key == "GEMINI_API_KEY":
        # Probe which model this key can actually run — capped at 2.5-flash
        # and below unless the tenant already opted into premium models —
        # and pin the tenant to it. Falls back to Ollama at chat time if no
        # Gemini model works at all (handled in llm.py, not here).
        allow_premium = backend.get(tenant.id, "GEMINI_ALLOW_PREMIUM") == "1"
        access = await resolve_gemini_access(value, allow_premium=allow_premium)
        if not access["ok"]:
            raise HTTPException(status_code=422, detail=access["reason"])
        backend.set(tenant.id, "GEMINI_API_KEY", value)
        backend.set(tenant.id, "GEMINI_MODEL", access["model"])
        backend.set(tenant.id, "GEMINI_TIER", "free" if access["limited"] else "standard")
        backend.set(tenant.id, "LLM_ENGINE", "gemini")
        runtime.on_secrets_changed(tenant.engine_scope)
        return {
            "ok": True,
            "validation": access["reason"],
            "model": access["model"],
            "limited": access["limited"],
        }

    ok, message = await validate_secret(body.key, value)
    if not ok:
        raise HTTPException(status_code=422, detail=message)

    backend.set(tenant.id, body.key, value)
    # A stale LLM client / tool binding must not keep using the old key.
    runtime.on_secrets_changed(tenant.engine_scope)
    return {"ok": True, "validation": message}


@router.delete("/{key}")
async def delete_secret(key: str, tenant: Tenant = Depends(require_session)):
    if key not in ALLOWED_KEYS:
        raise HTTPException(status_code=400, detail="unknown_secret_key")
    get_secrets_backend().delete(tenant.id, key)
    runtime.on_secrets_changed(tenant.engine_scope)
    return {"ok": True}

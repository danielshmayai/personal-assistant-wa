"""Current-tenant profile, status and account deletion."""
from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import runtime
from product.deps import require_session
from product.modules.registry import MODULES, allowed_ids_for
from product.modules.store import get_enabled_modules
from product.offboarding import delete_tenant_data
from product.secrets import get_secrets_backend
from product.secrets.catalog import SECRET_SPECS
from product.tenancy.models import Tenant

router = APIRouter(prefix="/api")


@router.get("/me")
async def me(tenant: Tenant = Depends(require_session)):
    backend = get_secrets_backend()
    present = backend.list_present(tenant.id)
    enabled = set(get_enabled_modules(tenant.id, tenant.is_owner))
    allowed = allowed_ids_for(tenant.is_owner)

    # Which model/engine this account is actually running on right now
    # (pinned at key-save/self-heal time). Not sensitive — always shown in
    # the UI, not just when free-tier-limited or on the Ollama fallback.
    from app.config import GEMINI_MODEL
    if tenant.is_owner:
        # Owner scope never probes/pins/falls back (backend/app/llm.py's
        # _resolve_engine_and_model short-circuits to the env default) — so
        # report that same truth here instead of reading tenant_secrets,
        # which the engine never writes to for the owner.
        gemini_model, gemini_limited, llm_engine, allow_premium = GEMINI_MODEL, False, "gemini", False
    else:
        gemini_model = backend.get(tenant.id, "GEMINI_MODEL") or GEMINI_MODEL
        gemini_limited = backend.get(tenant.id, "GEMINI_TIER") == "free"
        llm_engine = backend.get(tenant.id, "LLM_ENGINE") or "gemini"
        allow_premium = backend.get(tenant.id, "GEMINI_ALLOW_PREMIUM") == "1"

    # _token_key resolves "web*" chat_ids via the tenant ContextVar that
    # require_session just set, so any web-prefixed probe id works here.
    from app.google.auth import get_credentials
    google_connected = bool(get_credentials("web_probe"))

    return {
        "tenant": {
            "id": tenant.id,
            "email": tenant.email,
            "display_name": tenant.display_name,
            "avatar_url": tenant.avatar_url,
            "is_owner": tenant.is_owner,
            "onboarded": tenant.onboarded_at is not None,
        },
        "modules": [
            {
                **asdict(m),
                "enabled": m.id in enabled,
                "available": m.id in allowed,
                "missing_secrets": [k for k in m.secret_keys if k not in present],
            }
            for m in MODULES
        ],
        "secrets": [
            {"key": s.key, "label": s.label, "module": s.module, "required": s.required,
             "help_url": s.help_url, "placeholder": s.placeholder, "set": s.key in present}
            for s in SECRET_SPECS
        ],
        "google_connected": google_connected,
        "gemini": {
            "model": gemini_model,
            "limited": gemini_limited,
            "engine": llm_engine,
            "allow_premium": allow_premium,
            "can_set_premium": not tenant.is_owner,
        },
    }


class ModelPreference(BaseModel):
    allow_premium: bool


@router.put("/me/model-preference")
async def set_model_preference(body: ModelPreference, tenant: Tenant = Depends(require_session)):
    """Opt in/out of models stronger than Gemini 2.5 Flash (e.g. the "pro"
    tier). Off by default — the assistant never auto-escalates past 2.5
    Flash and below. Clears the tenant's existing model pin so the very next
    message re-probes against the new tier ceiling instead of keeping
    whatever was pinned before this changed."""
    if tenant.is_owner:
        return {"ok": False, "error": "owner_uses_platform_default"}
    backend = get_secrets_backend()
    backend.set(tenant.id, "GEMINI_ALLOW_PREMIUM", "1" if body.allow_premium else "0")
    backend.delete(tenant.id, "GEMINI_MODEL")
    backend.delete(tenant.id, "LLM_ENGINE")
    runtime.on_secrets_changed(tenant.engine_scope)
    return {"ok": True}


@router.delete("/me")
async def delete_me(tenant: Tenant = Depends(require_session)):
    if tenant.is_owner:
        return {"ok": False, "error": "owner_cannot_self_delete"}
    await delete_tenant_data(tenant.id, tenant.engine_scope)
    return {"ok": True}

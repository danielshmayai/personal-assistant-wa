"""Current-tenant profile, status and account deletion."""
from dataclasses import asdict

from fastapi import APIRouter, Depends

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

    # Which Gemini model this account runs on (pinned at key-save time for
    # free-tier keys). Not a sensitive value — safe to show in the UI.
    from app.config import GEMINI_MODEL
    gemini_model = backend.get(tenant.id, "GEMINI_MODEL") or GEMINI_MODEL
    gemini_limited = backend.get(tenant.id, "GEMINI_TIER") == "free"

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
        "gemini": {"model": gemini_model, "limited": gemini_limited},
    }


@router.delete("/me")
async def delete_me(tenant: Tenant = Depends(require_session)):
    if tenant.is_owner:
        return {"ok": False, "error": "owner_cannot_self_delete"}
    await delete_tenant_data(tenant.id, tenant.engine_scope)
    return {"ok": True}

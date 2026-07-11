"""Per-tenant module enable/disable state."""
from product.db import get_conn
from product.modules.registry import MODULES, MODULES_BY_ID, allowed_ids_for, default_enabled_ids


def get_enabled_modules(tenant_id: str, is_owner: bool) -> list[str]:
    """Enabled module ids for a tenant (defaults applied for unset modules)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT module_id, enabled FROM tenant_modules WHERE tenant_id = %s",
                (tenant_id,),
            )
            saved = {r[0]: r[1] for r in cur.fetchall()}
    finally:
        conn.close()

    allowed = allowed_ids_for(is_owner)
    enabled = []
    for m in MODULES:
        if m.id not in allowed:
            continue
        if m.core or saved.get(m.id, m.default_on):
            enabled.append(m.id)
    return enabled


def set_module(tenant_id: str, module_id: str, enabled: bool, is_owner: bool) -> None:
    mod = MODULES_BY_ID.get(module_id)
    if mod is None:
        raise ValueError(f"unknown module: {module_id}")
    if mod.core and not enabled:
        raise ValueError(f"module {module_id} is core and cannot be disabled")
    if module_id not in allowed_ids_for(is_owner):
        raise ValueError(f"module {module_id} is not available for this account")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tenant_modules (tenant_id, module_id, enabled)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (tenant_id, module_id) DO UPDATE SET enabled = EXCLUDED.enabled""",
                (tenant_id, module_id, enabled),
            )
        conn.commit()
    finally:
        conn.close()


def set_modules_bulk(tenant_id: str, enabled_ids: list[str], is_owner: bool) -> None:
    """Onboarding: set the whole selection at once."""
    allowed = allowed_ids_for(is_owner)
    selection = set(enabled_ids) | {m.id for m in MODULES if m.core}
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for m in MODULES:
                if m.id not in allowed:
                    continue
                cur.execute(
                    """INSERT INTO tenant_modules (tenant_id, module_id, enabled)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (tenant_id, module_id) DO UPDATE SET enabled = EXCLUDED.enabled""",
                    (tenant_id, m.id, m.id in selection),
                )
        conn.commit()
    finally:
        conn.close()


def _ensure_defaults(tenant_id: str) -> None:
    """Seed default rows on first login (idempotent)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for mid in default_enabled_ids():
                cur.execute(
                    """INSERT INTO tenant_modules (tenant_id, module_id, enabled)
                       VALUES (%s, %s, TRUE)
                       ON CONFLICT (tenant_id, module_id) DO NOTHING""",
                    (tenant_id, mid),
                )
        conn.commit()
    finally:
        conn.close()

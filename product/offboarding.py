"""Full tenant data deletion (GDPR-style offboarding).

Removes: vault directory, encrypted secrets + DEK, engine memory rows,
Google resource tokens, LangGraph checkpoints, product rows (sessions,
modules, identity), and finally marks the tenant row deleted (the row is
kept as a tombstone so the email can't silently re-register as a "new"
user with lingering artifacts elsewhere).
"""
import logging

from product.db import get_conn
from product.secrets import get_secrets_backend
from product.storage import get_vault_storage
from product.tenancy.store import revoke_all_sessions, set_tenant_status

logger = logging.getLogger("product.offboarding")

# Engine tables scoped by tenant_id column
_ENGINE_TENANT_TABLES = (
    "memory_facts", "memory_rules", "vault_embeddings", "rule_embeddings",
    "episodes", "conversation_log", "web_conversations",
)
# LangGraph checkpoint tables scoped by thread_id prefix
_CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


async def delete_tenant_data(tenant_id: str, engine_scope: str) -> None:
    """Irreversibly delete everything a tenant owns. Owner cannot be deleted."""
    if not engine_scope:
        raise ValueError("refusing to delete the owner scope")

    revoke_all_sessions(tenant_id)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for table in _ENGINE_TENANT_TABLES:
                cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (engine_scope,))
            # Google resource tokens are keyed "tenant:<engine_scope>"
            cur.execute("DELETE FROM google_tokens WHERE chat_id = %s", (f"tenant:{engine_scope}",))
            # Conversation threads are chat_id = web_<engine_scope>_<session>
            for table in _CHECKPOINT_TABLES:
                try:
                    cur.execute(
                        f"DELETE FROM {table} WHERE thread_id LIKE %s",
                        (f"web_{engine_scope}_%",),
                    )
                except Exception:
                    logger.debug("checkpoint table %s cleanup skipped", table, exc_info=True)
            # Product rows (sessions/modules/identity/secrets cascade on tenants FK,
            # but the tombstone keeps the tenants row — delete children explicitly).
            cur.execute("DELETE FROM sessions WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM tenant_modules WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM tenant_google_identity WHERE tenant_id = %s", (tenant_id,))
        conn.commit()
    finally:
        conn.close()

    get_secrets_backend().delete_all(tenant_id)
    get_vault_storage().delete_tenant_data(engine_scope)
    set_tenant_status(tenant_id, "deleted")
    logger.info("Tenant data deleted", extra={"tenant_id": tenant_id})

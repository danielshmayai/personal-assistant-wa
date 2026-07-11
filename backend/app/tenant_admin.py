"""Owner-only tenant approval from WhatsApp.

The product app (docker-compose.product.yml) owns the tenancy model, but its
`tenants` table lives in the SAME Postgres this engine uses. So the owner can
approve/deny pending web sign-ups straight from the danidin self-chat without
opening the product UI — we talk to the shared table directly here.

Fails soft: if the product stack has never run (no `tenants` table), the
commands report that gracefully instead of raising.
"""
import logging

import psycopg2

from app.memory.store import _get_conn as get_conn

logger = logging.getLogger("pa.tenant_admin")


def _table_missing(exc: Exception) -> bool:
    return isinstance(exc, psycopg2.errors.UndefinedTable)


def list_pending() -> list[str]:
    """Emails of accounts awaiting approval, oldest first."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email FROM tenants WHERE status = 'pending' ORDER BY created_at"
            )
            return [r[0] for r in cur.fetchall()]
    except Exception as exc:
        if _table_missing(exc):
            return []
        raise
    finally:
        conn.close()


def set_status(ident: str, status: str) -> tuple[bool, str]:
    """Set a tenant's status by email (or id). Returns (ok, email_or_message).

    Never touches the owner row. Only pending/revoked/active tenants are
    eligible — a deleted tenant stays deleted.
    """
    ident = ident.strip()
    if not ident:
        return False, "Usage: approve <email>  (or: deny <email>)"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, is_owner, status FROM tenants "
                "WHERE lower(email) = lower(%s) OR id = %s",
                (ident, ident),
            )
            row = cur.fetchone()
            if not row:
                return False, f"No account found for '{ident}'."
            tid, email, is_owner, current = row
            if is_owner:
                return False, "That's the owner account — nothing to change."
            if current == "deleted":
                return False, f"{email} was deleted and can't be changed."
            cur.execute("UPDATE tenants SET status = %s WHERE id = %s", (status, tid))
        conn.commit()
        logger.info("Tenant %s (%s) status → %s via WhatsApp", tid, email, status)
        return True, email
    except Exception as exc:
        if _table_missing(exc):
            return False, "The web/product stack isn't set up yet (no tenants table)."
        raise
    finally:
        conn.close()

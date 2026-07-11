"""Tenant + session persistence."""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from product.config import OWNER_EMAIL, SESSION_TTL_DAYS
from product.db import get_conn
from product.tenancy.models import Tenant

logger = logging.getLogger("product.tenancy")

_TENANT_COLS = "id, email, display_name, avatar_url, is_owner, status, onboarded_at"
_TENANT_COLS_T = "t.id, t.email, t.display_name, t.avatar_url, t.is_owner, t.status, t.onboarded_at"


def _row_to_tenant(row) -> Tenant:
    return Tenant(
        id=row[0], email=row[1], display_name=row[2], avatar_url=row[3],
        is_owner=row[4], status=row[5], onboarded_at=row[6],
    )


def get_tenant(tenant_id: str) -> Tenant | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_TENANT_COLS} FROM tenants WHERE id = %s", (tenant_id,))
            row = cur.fetchone()
            return _row_to_tenant(row) if row else None
    finally:
        conn.close()


def list_tenants() -> list[Tenant]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_TENANT_COLS} FROM tenants ORDER BY created_at")
            return [_row_to_tenant(r) for r in cur.fetchall()]
    finally:
        conn.close()


def upsert_tenant_from_google(sub: str, email: str, name: str, picture: str) -> tuple[Tenant, bool]:
    """Find-or-create a tenant for a verified Google identity.

    Matching order: google_sub link → email. The configured OWNER_EMAIL
    (or the very first tenant ever created) becomes the owner and is active
    immediately; every other new tenant is created 'pending' and stays
    blocked until the owner approves it.

    Returns (tenant, created) — created is True only when a brand-new tenant
    row was inserted, so callers can notify the owner exactly once.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT {_TENANT_COLS_T} FROM tenants t
                    JOIN tenant_google_identity g ON g.tenant_id = t.id
                    WHERE g.google_sub = %s""",
                (sub,),
            )
            row = cur.fetchone()
            if row:
                return _row_to_tenant(row), False

            cur.execute(f"SELECT {_TENANT_COLS} FROM tenants WHERE email = %s", (email,))
            row = cur.fetchone()
            created = False
            if row:
                tenant = _row_to_tenant(row)
                if tenant.status == "deleted":
                    # A previously-deleted account signing in again. Its data
                    # was already wiped at offboarding, so there are no lingering
                    # artifacts — let the person return as a fresh pending request
                    # (re-onboard, owner re-approves) instead of a dead tombstone.
                    cur.execute(
                        "UPDATE tenants SET status = 'pending', onboarded_at = NULL, "
                        "display_name = %s, avatar_url = %s WHERE id = %s",
                        (name or tenant.display_name, picture or tenant.avatar_url, tenant.id),
                    )
                    tenant.status = "pending"
                    tenant.onboarded_at = None
                    tenant.display_name = name or tenant.display_name
                    tenant.avatar_url = picture or tenant.avatar_url
                    created = True  # notify the owner, just like a brand-new request
            else:
                cur.execute("SELECT COUNT(*) FROM tenants")
                first_ever = cur.fetchone()[0] == 0
                is_owner = (email == OWNER_EMAIL) or (not OWNER_EMAIL and first_ever)
                # New strangers land in 'pending' and cannot get a session
                # until the owner approves (get_session_tenant filters active).
                status = "active" if is_owner else "pending"
                tenant = Tenant(
                    id=str(uuid.uuid4()), email=email,
                    display_name=name or "", avatar_url=picture or "",
                    is_owner=is_owner, status=status,
                )
                cur.execute(
                    """INSERT INTO tenants (id, email, display_name, avatar_url, is_owner, status)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (tenant.id, tenant.email, tenant.display_name, tenant.avatar_url,
                     tenant.is_owner, tenant.status),
                )
                created = True
                logger.info("Created tenant %s (%s, owner=%s, status=%s)",
                            tenant.id, email, is_owner, status)

            cur.execute(
                """INSERT INTO tenant_google_identity (google_sub, tenant_id)
                   VALUES (%s, %s) ON CONFLICT (google_sub) DO NOTHING""",
                (sub, tenant.id),
            )
        conn.commit()
        return tenant, created
    finally:
        conn.close()


def mark_onboarded(tenant_id: str) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tenants SET onboarded_at = NOW() WHERE id = %s AND onboarded_at IS NULL",
                (tenant_id,),
            )
        conn.commit()
    finally:
        conn.close()


def set_tenant_status(tenant_id: str, status: str) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE tenants SET status = %s WHERE id = %s", (status, tenant_id))
        conn.commit()
    finally:
        conn.close()


# ── Sessions ────────────────────────────────────────────────────────────────

def create_session(tenant_id: str) -> tuple[str, datetime]:
    jti = str(uuid.uuid4())
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (id, tenant_id, expires_at) VALUES (%s, %s, %s)",
                (jti, tenant_id, expires),
            )
        conn.commit()
        return jti, expires
    finally:
        conn.close()


def get_session_tenant(jti: str) -> Tenant | None:
    """Return the tenant for a live (unrevoked, unexpired, active) session."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT {_TENANT_COLS_T} FROM tenants t
                    JOIN sessions s ON s.tenant_id = t.id
                    WHERE s.id = %s AND NOT s.revoked AND s.expires_at > NOW()
                          AND t.status = 'active'""",
                (jti,),
            )
            row = cur.fetchone()
            return _row_to_tenant(row) if row else None
    finally:
        conn.close()


def revoke_session(jti: str) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE sessions SET revoked = TRUE WHERE id = %s", (jti,))
        conn.commit()
    finally:
        conn.close()


def revoke_all_sessions(tenant_id: str) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE sessions SET revoked = TRUE WHERE tenant_id = %s", (tenant_id,))
        conn.commit()
    finally:
        conn.close()

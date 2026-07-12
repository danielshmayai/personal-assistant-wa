"""Garmin Connect client: one-time login (with optional MFA), encrypted token persistence.

The garminconnect library's native DI-OAuth token bundle (``garmin.client.dumps()`` —
a JSON string of di_token/di_refresh_token/di_client_id) is stored Fernet-encrypted in
Postgres (same pattern as google_tokens). Tokens auto-refresh, so after the one-time
login everything is automatic.
"""
from __future__ import annotations

import asyncio
import logging
import threading

import psycopg2

from app import crypto
from app.config import DATABASE_URL

logger = logging.getLogger("pa.garmin")

_lock = threading.Lock()
# Per-tenant state ('' = owner). One Garmin session per tenant, never shared.
_client_cache: dict[str, object] = {}       # tenant_id -> garminconnect.Garmin
_pending_mfa: dict[str, tuple] = {}          # tenant_id -> (Garmin, login state)


def _scope() -> str:
    from app import runtime
    return runtime.tenant_id()


class GarminNotConnected(Exception):
    """Raised when no valid Garmin session exists."""


# ── Tables ──────────────────────────────────────────────────────────────────

def _pk_cols(cur, table: str) -> set[str]:
    cur.execute(
        """
        SELECT a.attname FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = %s::regclass AND i.indisprimary
        """,
        (table,),
    )
    return {r[0] for r in cur.fetchall()}


def init_table() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS garmin_tokens (
                    tenant_id TEXT PRIMARY KEY DEFAULT '',
                    token_blob TEXT NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            # Legacy single-row schema (id=1) → tenant-keyed; the existing
            # row becomes the owner row (tenant_id='').
            cur.execute(
                "ALTER TABLE garmin_tokens ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT ''"
            )
            if _pk_cols(cur, "garmin_tokens") != {"tenant_id"}:
                cur.execute("ALTER TABLE garmin_tokens DROP CONSTRAINT IF EXISTS garmin_tokens_pkey")
                cur.execute("ALTER TABLE garmin_tokens ADD PRIMARY KEY (tenant_id)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS garmin_wellness (
                    log_date DATE PRIMARY KEY,
                    sleep_score INT,
                    sleep_min INT,
                    bb_high INT,
                    bb_low INT,
                    bb_latest INT,
                    stress_avg INT,
                    resting_hr INT,
                    steps INT,
                    raw JSONB DEFAULT '{}',
                    synced_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        conn.commit()
    finally:
        conn.close()


# ── Token persistence ───────────────────────────────────────────────────────

def save_token_blob(blob: str) -> None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO garmin_tokens (tenant_id, token_blob, updated_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (tenant_id) DO UPDATE SET token_blob = EXCLUDED.token_blob,
                                                         updated_at = NOW()""",
                (_scope(), crypto.encrypt(blob)),
            )
        conn.commit()
    finally:
        conn.close()


def load_token_blob() -> str | None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT token_blob FROM garmin_tokens WHERE tenant_id = %s", (_scope(),)
            )
            row = cur.fetchone()
        return crypto.decrypt(row[0]) if row else None
    finally:
        conn.close()


def _delete_token_blob() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM garmin_tokens WHERE tenant_id = %s", (_scope(),))
        conn.commit()
    finally:
        conn.close()


def is_connected() -> bool:
    try:
        return load_token_blob() is not None
    except Exception:
        return False


# ── Client lifecycle (all blocking; call via asyncio.to_thread) ─────────────

def get_client():
    """Return a logged-in Garmin client for the current tenant scope."""
    tid = _scope()
    with _lock:
        cached = _client_cache.get(tid)
        if cached is not None:
            return cached
        blob = load_token_blob()
        if not blob:
            raise GarminNotConnected("No Garmin session — connect first")
        from garminconnect import Garmin
        g = Garmin()
        try:
            g.login(blob)
        except Exception as exc:
            logger.warning("Garmin token resume failed: %s", exc)
            raise GarminNotConnected(f"Garmin session expired: {exc}") from exc
        _client_cache[tid] = g
        return g


def _refresh_saved_tokens(g) -> None:
    """Persist possibly-refreshed tokens so the stored blob stays current."""
    try:
        save_token_blob(g.client.dumps())
    except Exception:
        logger.debug("could not persist refreshed Garmin tokens", exc_info=True)


def invalidate_client() -> None:
    with _lock:
        _client_cache.pop(_scope(), None)


def _friendly_login_error(exc: Exception) -> str:
    from garminconnect.exceptions import (
        GarminConnectTooManyRequestsError,
        GarminConnectAuthenticationError,
    )
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return "Garmin rate-limited this login — wait a while and try again"
    if isinstance(exc, GarminConnectAuthenticationError):
        return f"Garmin rejected the credentials: {exc}"
    return str(exc)


def begin_login(email: str, password: str) -> dict:
    """Start a login. Returns {'connected': True} or {'mfa_required': True}."""
    tid = _scope()
    from garminconnect import Garmin
    g = Garmin(email=email, password=password, return_on_mfa=True)
    try:
        result1, result2 = g.login()
    except Exception as exc:
        raise RuntimeError(_friendly_login_error(exc)) from exc
    if result1 == "needs_mfa":
        with _lock:
            _pending_mfa[tid] = (g, result2)
        return {"mfa_required": True}
    save_token_blob(g.client.dumps())
    with _lock:
        _client_cache[tid] = g
        _pending_mfa.pop(tid, None)
    logger.info("Garmin connected")
    return {"connected": True}


def finish_mfa(code: str) -> dict:
    """Complete a pending MFA login with the emailed/app code."""
    tid = _scope()
    with _lock:
        pending = _pending_mfa.get(tid)
    if not pending:
        raise GarminNotConnected("No pending Garmin login — start again")
    g, state = pending
    try:
        g.resume_login(state, code.strip())
    except Exception as exc:
        raise RuntimeError(_friendly_login_error(exc)) from exc
    save_token_blob(g.client.dumps())
    with _lock:
        _client_cache[tid] = g
        _pending_mfa.pop(tid, None)
    logger.info("Garmin connected (MFA)")
    return {"connected": True}


def disconnect() -> None:
    _delete_token_blob()
    invalidate_client()
    with _lock:
        _pending_mfa.pop(_scope(), None)
    logger.info("Garmin disconnected")


# ── Async wrappers ──────────────────────────────────────────────────────────

async def call(fn_name: str, *args, **kwargs):
    """Run a garminconnect method off the event loop; retries once after re-login."""
    def _run():
        g = get_client()
        try:
            out = getattr(g, fn_name)(*args, **kwargs)
        except Exception:
            # One retry with a fresh session (token may have just expired/refreshed).
            invalidate_client()
            g2 = get_client()
            out = getattr(g2, fn_name)(*args, **kwargs)
            _refresh_saved_tokens(g2)
            return out
        return out
    return await asyncio.to_thread(_run)

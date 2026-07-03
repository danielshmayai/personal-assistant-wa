"""Garmin Connect client: one-time login (with optional MFA), encrypted token persistence.

The garth OAuth token bundle (``garmin.garth.dumps()`` — a base64 blob) is stored
Fernet-encrypted in Postgres (same pattern as google_tokens). Tokens auto-refresh for
about a year, so after the one-time login everything is automatic.
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
_client_cache = None          # cached garminconnect.Garmin instance
_pending_mfa: tuple | None = None  # (Garmin instance, login state) awaiting an MFA code


class GarminNotConnected(Exception):
    """Raised when no valid Garmin session exists."""


# ── Tables ──────────────────────────────────────────────────────────────────

def init_table() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS garmin_tokens (
                    id INT PRIMARY KEY DEFAULT 1,
                    token_blob TEXT NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
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
                """INSERT INTO garmin_tokens (id, token_blob, updated_at)
                   VALUES (1, %s, NOW())
                   ON CONFLICT (id) DO UPDATE SET token_blob = EXCLUDED.token_blob,
                                                  updated_at = NOW()""",
                (crypto.encrypt(blob),),
            )
        conn.commit()
    finally:
        conn.close()


def load_token_blob() -> str | None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT token_blob FROM garmin_tokens WHERE id = 1")
            row = cur.fetchone()
        return crypto.decrypt(row[0]) if row else None
    finally:
        conn.close()


def _delete_token_blob() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM garmin_tokens WHERE id = 1")
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
    """Return a logged-in Garmin client, resuming from the stored token blob."""
    global _client_cache
    with _lock:
        if _client_cache is not None:
            return _client_cache
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
        _client_cache = g
        return g


def _refresh_saved_tokens(g) -> None:
    """Persist possibly-refreshed tokens so the stored blob stays current."""
    try:
        save_token_blob(g.garth.dumps())
    except Exception:
        logger.debug("could not persist refreshed Garmin tokens", exc_info=True)


def invalidate_client() -> None:
    global _client_cache
    with _lock:
        _client_cache = None


def begin_login(email: str, password: str) -> dict:
    """Start a login. Returns {'connected': True} or {'mfa_required': True}."""
    global _pending_mfa, _client_cache
    from garminconnect import Garmin
    g = Garmin(email=email, password=password, return_on_mfa=True)
    result1, result2 = g.login()
    if result1 == "needs_mfa":
        with _lock:
            _pending_mfa = (g, result2)
        return {"mfa_required": True}
    save_token_blob(g.garth.dumps())
    with _lock:
        _client_cache = g
        _pending_mfa = None
    logger.info("Garmin connected")
    return {"connected": True}


def finish_mfa(code: str) -> dict:
    """Complete a pending MFA login with the emailed/app code."""
    global _pending_mfa, _client_cache
    with _lock:
        pending = _pending_mfa
    if not pending:
        raise GarminNotConnected("No pending Garmin login — start again")
    g, state = pending
    g.resume_login(state, code.strip())
    save_token_blob(g.garth.dumps())
    with _lock:
        _client_cache = g
        _pending_mfa = None
    logger.info("Garmin connected (MFA)")
    return {"connected": True}


def disconnect() -> None:
    global _pending_mfa
    _delete_token_blob()
    invalidate_client()
    with _lock:
        _pending_mfa = None
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

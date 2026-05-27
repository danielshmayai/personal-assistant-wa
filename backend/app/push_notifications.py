"""Web Push Notifications via VAPID.

Setup:
  1. Generate keys once:
       docker compose exec backend python -c \
         "from app.push_notifications import print_vapid_keys; print_vapid_keys()"
  2. Add VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY to .env.
  3. Restart — the app will use the persistent keys.

If keys are absent, ephemeral keys are auto-generated on each startup.
Existing push subscriptions become invalid after a key rotation.
"""
from __future__ import annotations

import base64
import json
import logging

import psycopg2

from app.config import DATABASE_URL, VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, VAPID_SUBJECT

logger = logging.getLogger("pa.push")

# ---------------------------------------------------------------------------
# VAPID key management
# ---------------------------------------------------------------------------

_cached_private_key: str | None = None
_cached_public_key: str | None = None


def _ensure_vapid_keys() -> tuple[str, str]:
    """Return (private_key_pem, public_key_b64url). Auto-generates once if not configured."""
    global _cached_private_key, _cached_public_key

    if _cached_private_key and _cached_public_key:
        return _cached_private_key, _cached_public_key

    priv = VAPID_PRIVATE_KEY.replace("\\n", "\n").strip()
    pub = VAPID_PUBLIC_KEY.strip()

    if priv and pub:
        _cached_private_key, _cached_public_key = priv, pub
        return priv, pub

    # Auto-generate ephemeral keys
    logger.warning(
        "VAPID_PRIVATE_KEY not set — generating ephemeral keys. "
        "Push subscriptions will break on restart. Run print_vapid_keys() and add to .env."
    )
    priv, pub = _generate_vapid_pair()
    logger.warning("VAPID_PRIVATE_KEY=%s", priv.replace("\n", "\\n"))
    logger.warning("VAPID_PUBLIC_KEY=%s", pub)

    _cached_private_key, _cached_public_key = priv, pub
    return priv, pub


def _generate_vapid_pair() -> tuple[str, str]:
    """Generate a new VAPID key pair. Returns (pem_str, base64url_public_key)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend

    key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    pub_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    pub = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode("utf-8")
    return pem, pub


def print_vapid_keys() -> None:
    """Print a fresh VAPID key pair for copying into .env."""
    pem, pub = _generate_vapid_pair()
    print("# Add these to your .env file:")
    print(f"VAPID_PRIVATE_KEY={pem.replace(chr(10), chr(92) + 'n')}")
    print(f"VAPID_PUBLIC_KEY={pub}")


def get_vapid_public_key() -> str:
    """Return the VAPID public key (base64url) for the browser applicationServerKey."""
    _, pub = _ensure_vapid_keys()
    return pub


# ---------------------------------------------------------------------------
# Push subscription DB
# ---------------------------------------------------------------------------

def init_push_table() -> None:
    """Create push_subscriptions table if absent."""
    if not DATABASE_URL:
        return
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    id                SERIAL PRIMARY KEY,
                    chat_id           TEXT NOT NULL,
                    endpoint          TEXT NOT NULL UNIQUE,
                    subscription_json TEXT NOT NULL,
                    created_at        TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_push_subs_chat_id "
                "ON push_subscriptions (chat_id)"
            )
        conn.commit()
    finally:
        conn.close()


def save_push_subscription(chat_id: str, subscription: dict) -> None:
    """Upsert a push subscription."""
    if not DATABASE_URL:
        return
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO push_subscriptions (chat_id, endpoint, subscription_json)
                VALUES (%s, %s, %s)
                ON CONFLICT (endpoint) DO UPDATE
                  SET chat_id = EXCLUDED.chat_id,
                      subscription_json = EXCLUDED.subscription_json
                """,
                (chat_id, subscription["endpoint"], json.dumps(subscription)),
            )
        conn.commit()
    finally:
        conn.close()


def get_push_subscriptions(chat_id: str) -> list[dict]:
    """Return all push subscriptions for a chat_id."""
    if not DATABASE_URL:
        return []
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT subscription_json FROM push_subscriptions WHERE chat_id = %s",
                (chat_id,),
            )
            return [json.loads(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()


def delete_push_subscription(endpoint: str) -> None:
    """Remove a stale/expired subscription."""
    if not DATABASE_URL:
        return
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,)
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Push sender
# ---------------------------------------------------------------------------

def send_web_push_sync(subscription: dict, title: str, body: str) -> None:
    """Send a single Web Push notification (synchronous — call via asyncio.to_thread)."""
    from pywebpush import webpush, WebPushException

    priv, _ = _ensure_vapid_keys()
    subject = VAPID_SUBJECT or "mailto:admin@example.com"

    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=priv,
            vapid_claims={"sub": subject},
            timeout=10,
        )
        logger.debug("Push sent to %s…", subscription.get("endpoint", "")[:50])
    except WebPushException as exc:
        resp = getattr(exc, "response", None)
        status = resp.status_code if resp is not None else None
        if status in (404, 410):
            logger.info("Removing expired push subscription: %s", subscription.get("endpoint", "")[:60])
            delete_push_subscription(subscription["endpoint"])
        else:
            logger.warning("WebPush failed (status=%s): %s", status, exc)
    except Exception as exc:
        logger.warning("WebPush unexpected error: %s", exc)

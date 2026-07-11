"""Session cookies: compact HMAC-signed tokens (stdlib only, no JWT dep).

Format: base64url(json payload) + "." + base64url(HMAC-SHA256(payload)).
Payload: {"jti": session-row-id, "tid": tenant_id, "exp": unix-ts}.
The DB session row is the source of truth (revocation, tenant status);
the signature only proves we issued the jti.
"""
import base64
import hashlib
import hmac
import json
import time

from product.config import SESSION_SECRET

COOKIE_NAME = "pa_session"


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: bytes) -> str:
    if not SESSION_SECRET:
        raise RuntimeError("SESSION_SECRET is not configured")
    return _b64e(hmac.new(SESSION_SECRET.encode(), payload, hashlib.sha256).digest())


def issue_token(jti: str, tenant_id: str, expires_at_unix: int) -> str:
    payload = json.dumps({"jti": jti, "tid": tenant_id, "exp": expires_at_unix}).encode()
    return f"{_b64e(payload)}.{_sign(payload)}"


def verify_token(token: str) -> dict | None:
    """Return the payload dict if the signature is valid and unexpired."""
    try:
        body, sig = token.split(".", 1)
        payload = _b64d(body)
        if not hmac.compare_digest(sig, _sign(payload)):
            return None
        data = json.loads(payload)
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None

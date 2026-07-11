"""Sign in with Google (OpenID Connect) using the platform OAuth client.

Login-only scopes (openid email profile). Gmail/Calendar/Drive consent is a
separate incremental flow (product/routers/google_connect.py) so the login
screen stays minimal.
"""
import logging
import secrets as pysecrets

import httpx

from product.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, PRODUCT_BASE_URL

logger = logging.getLogger("product.oidc")

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Login state nonces — persisted in the engine's oauth_pending_states table
# (same survives-restart pattern), namespaced with this marker as chat_id.
_LOGIN_STATE_MARKER = "product_login"


def redirect_uri() -> str:
    return f"{PRODUCT_BASE_URL}/auth/callback"


def build_login_url() -> str:
    from app.memory.store import save_oauth_state

    state = pysecrets.token_urlsafe(32)
    save_oauth_state(state, _LOGIN_STATE_MARKER)
    params = httpx.QueryParams({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })
    return f"{_AUTH_ENDPOINT}?{params}"


def consume_login_state(state: str) -> bool:
    from app.memory.store import pop_oauth_state
    return pop_oauth_state(state) == _LOGIN_STATE_MARKER


async def exchange_code(code: str) -> dict:
    """Exchange the auth code and return the verified ID-token claims."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(_TOKEN_ENDPOINT, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        })
    resp.raise_for_status()
    id_token = resp.json().get("id_token", "")
    if not id_token:
        raise ValueError("Google token response had no id_token")
    return _verify_id_token(id_token)


def _verify_id_token(token: str) -> dict:
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    claims = google_id_token.verify_oauth2_token(
        token, google_requests.Request(), GOOGLE_CLIENT_ID
    )
    if not claims.get("email_verified", False):
        raise ValueError("Google account email is not verified")
    return claims

import logging
import secrets
import time
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from app.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI
from app.memory.store import save_google_token, load_google_token

logger = logging.getLogger("pa.google.auth")

# ── OAuth state / PKCE stores ─────────────────────────────────────────────
# nonce → (chat_id, created_timestamp)
_state_map: dict[str, tuple[str, float]] = {}
_STATE_TTL = 600  # 10 minutes — auth flow must complete within this window

# PKCE code verifiers keyed by the same nonce
_pending_verifiers: dict[str, str] = {}

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

CLIENT_CONFIG = {
    "web": {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uris": [GOOGLE_REDIRECT_URI],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


def _purge_expired_states() -> None:
    """Remove state entries older than _STATE_TTL to prevent unbounded memory growth."""
    cutoff = time.time() - _STATE_TTL
    expired = [k for k, (_, ts) in _state_map.items() if ts < cutoff]
    for k in expired:
        _state_map.pop(k, None)
        _pending_verifiers.pop(k, None)


def get_auth_url(chat_id: str) -> str:
    _purge_expired_states()

    # Use a cryptographically random nonce as the OAuth 'state' parameter.
    # Previously this was the chat_id (guessable) — a random nonce prevents CSRF:
    # an attacker who knows the WhatsApp number cannot forge a valid callback.
    nonce = secrets.token_urlsafe(32)
    _state_map[nonce] = (chat_id, time.time())

    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=nonce,
    )
    if hasattr(flow, "code_verifier") and flow.code_verifier:
        _pending_verifiers[nonce] = flow.code_verifier

    return auth_url


def handle_callback(code: str, state: str) -> None:
    entry = _state_map.pop(state, None)
    verifier = _pending_verifiers.pop(state, None)

    if not entry:
        raise ValueError("Unknown or expired OAuth state — possible CSRF attempt")

    chat_id, created_at = entry
    if time.time() - created_at > _STATE_TTL:
        raise ValueError("OAuth state has expired — please restart the Google auth flow")

    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES, state=state)
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    if verifier:
        flow.code_verifier = verifier
    flow.fetch_token(code=code)
    creds = flow.credentials
    save_google_token(chat_id, creds)
    logger.info("Google token saved for chat_id=%s", chat_id)


def get_credentials(chat_id: str) -> Credentials | None:
    token_data = load_google_token(chat_id)
    if not token_data:
        return None

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=token_data["scopes"].split(",") if token_data["scopes"] else SCOPES,
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_google_token(chat_id, creds)
        logger.info("Refreshed Google token for chat_id=%s", chat_id)

    return creds

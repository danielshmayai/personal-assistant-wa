import logging
import secrets
import datetime
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import app.config as _cfg
from app.memory.store import save_google_token, load_google_token, delete_google_token, save_oauth_state, pop_oauth_state, peek_oauth_state, delete_oauth_state

logger = logging.getLogger("pa.google.auth")

# PKCE code verifiers keyed by nonce -- these only live for the duration of one
# auth flow and don't need to survive restarts (user would re-initiate anyway).
_pending_verifiers: dict[str, str] = {}

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    # Drive: only files created or opened by this app (least-privilege)
    "https://www.googleapis.com/auth/drive.file",
]

def _client_config() -> dict:
    """Build the OAuth client config dict at call time so env vars are always current."""
    if not _cfg.GOOGLE_CLIENT_ID or not _cfg.GOOGLE_CLIENT_SECRET:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env. "
            "Get them from Google Cloud Console -> APIs & Services -> Credentials -> OAuth 2.0 Client IDs."
        )
    return {
        "web": {
            "client_id": _cfg.GOOGLE_CLIENT_ID,
            "client_secret": _cfg.GOOGLE_CLIENT_SECRET,
            "redirect_uris": [_cfg.GOOGLE_REDIRECT_URI],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def _token_key(chat_id: str) -> str:
    """Web conversations use a new chat_id every session, so all web users
    share a single stable token key.  WhatsApp threads keep their phone number."""
    return "web_user" if chat_id.startswith("web") else chat_id


def get_auth_url(chat_id: str) -> str:
    nonce = secrets.token_urlsafe(32)
    save_oauth_state(nonce, chat_id)  # persisted in Postgres -- survives restarts

    cfg = _client_config()
    flow = Flow.from_client_config(cfg, scopes=SCOPES)
    flow.redirect_uri = _cfg.GOOGLE_REDIRECT_URI
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
    # Peek first -- state is only deleted after the token is successfully saved,
    # so a failed token exchange does not burn the nonce and the user can retry.
    chat_id = peek_oauth_state(state)
    if not chat_id:
        raise ValueError("Unknown or expired OAuth state -- please start the auth flow again")

    verifier = _pending_verifiers.pop(state, None)
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, state=state)
    flow.redirect_uri = _cfg.GOOGLE_REDIRECT_URI
    if verifier:
        flow.code_verifier = verifier
    flow.fetch_token(code=code)
    creds = flow.credentials
    # google-auth-oauthlib may not set expiry on the Credentials object; pull it
    # from the underlying OAuth2Session so token_expiry is never stored as NULL.
    if creds.expiry is None:
        expires_at = getattr(flow, "oauth2session", None) and flow.oauth2session.token.get("expires_at")
        if expires_at:
            creds.expiry = datetime.datetime.utcfromtimestamp(expires_at)
    save_google_token(_token_key(chat_id), creds)
    delete_oauth_state(state)  # only delete after token is safely stored
    logger.info("Google token saved for key=%s (chat_id=%s)", _token_key(chat_id), chat_id)


def get_credentials(chat_id: str) -> Credentials | None:
    key = _token_key(chat_id)
    token_data = load_google_token(key)
    if not token_data:
        return None

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=_cfg.GOOGLE_CLIENT_ID,
        client_secret=_cfg.GOOGLE_CLIENT_SECRET,
        scopes=token_data["scopes"].split(",") if token_data["scopes"] else SCOPES,
        expiry=token_data["token_expiry"],
    )

    # Refresh if expired OR if expiry is None (token was saved before the expiry
    # fix and we can not tell whether it is still valid).
    if (creds.expired or creds.expiry is None) and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_google_token(key, creds)
            logger.info("Refreshed Google token for key=%s", key)
        except Exception as exc:
            logger.warning(
                "Token refresh failed for key=%s (%s) -- clearing token, re-auth required",
                key, exc,
            )
            delete_google_token(key)
            return None

    if creds.expired:
        logger.info("Credentials expired, no refresh_token for key=%s -- re-auth required", key)
        return None

    return creds

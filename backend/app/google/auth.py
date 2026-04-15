import logging
import secrets
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from app.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI
from app.memory.store import save_google_token, load_google_token, save_oauth_state, pop_oauth_state

logger = logging.getLogger("pa.google.auth")

# PKCE code verifiers keyed by nonce — these only live for the duration of one
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

CLIENT_CONFIG = {
    "web": {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uris": [GOOGLE_REDIRECT_URI],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}



def get_auth_url(chat_id: str) -> str:
    nonce = secrets.token_urlsafe(32)
    save_oauth_state(nonce, chat_id)  # persisted in Postgres — survives restarts

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
    chat_id = pop_oauth_state(state)  # atomic read-and-delete from Postgres
    if not chat_id:
        raise ValueError("Unknown or expired OAuth state — please start the auth flow again")

    verifier = _pending_verifiers.pop(state, None)
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES, state=state)
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    if verifier:
        flow.code_verifier = verifier
    flow.fetch_token(code=code)
    save_google_token(chat_id, flow.credentials)
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

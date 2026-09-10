import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from app.google.auth import OAuthStateExpired, get_auth_url, handle_callback

logger = logging.getLogger("pa.google.auth_router")

router = APIRouter()

_SUCCESS_HTML = """
<!DOCTYPE html>
<html>
<head><title>Connected</title></head>
<body style="font-family:sans-serif;text-align:center;padding:60px">
  <h2>Google account connected successfully.</h2>
  <p>You can close this tab and return to WhatsApp.</p>
</body>
</html>
"""

_ERROR_HTML = """
<!DOCTYPE html>
<html>
<head><title>Error</title></head>
<body style="font-family:sans-serif;text-align:center;padding:60px">
  <h2>Failed to connect Google account.</h2>
  <p>{error}</p>
</body>
</html>
"""


@router.get("/auth/google/start")
async def google_auth_start(chat_id: str):
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    auth_url = get_auth_url(chat_id)
    return {"auth_url": auth_url}


@router.get("/auth/google/callback")
async def google_auth_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        logger.warning("Google OAuth error: %s", error)
        return HTMLResponse(_ERROR_HTML.format(error=error), status_code=400)
    if not code or not state:
        return HTMLResponse(_ERROR_HTML.format(error="Missing code or state."), status_code=400)
    try:
        handle_callback(code, state)
    except OAuthStateExpired:
        # Almost always a link reused from chat history: auth links are
        # single-use and expire after 1 hour. Say so instead of blaming the
        # token exchange, which sent us chasing credentials for hours.
        logger.warning("Google OAuth link expired or already used (state=%s)", state)
        return HTMLResponse(
            _ERROR_HTML.format(
                error="This connection link has expired or was already used. "
                      "Auth links are valid for 1 hour — please ask for a new link and try again."
            ),
            status_code=400,
        )
    except Exception as exc:
        logger.exception("Failed to handle Google OAuth callback for state=%s", state)
        return HTMLResponse(
            _ERROR_HTML.format(error=f"Token exchange failed: {type(exc).__name__}: {exc}"),
            status_code=500,
        )
    return HTMLResponse(_SUCCESS_HTML)

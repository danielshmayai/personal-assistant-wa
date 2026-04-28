import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from app.config import (
    OLLAMA_BASE_URL, WAHA_BASE_URL, WAHA_API_KEY, WAHA_SESSION,
    WEBHOOK_SECRET, TEST_TOKEN,
)
from app.whatsapp import router as waha_router
from app.memory.store import init_memory_tables, _get_conn
from app.routers.google_auth import router as google_auth_router
from app.routers.web_chat import router as web_chat_router
from app.graph.checkpointer import setup_checkpointer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("pa.main")

WEBHOOK_EVENTS = ["message", "message.any"]

# IP-based rate limiter for public HTTP endpoints
limiter = Limiter(key_func=get_remote_address)


def _webhook_url() -> str:
    """Embed the shared secret in the callback URL so WAHA sends it on every request."""
    base = "http://backend:8000/webhook/waha"
    return f"{base}?secret={WEBHOOK_SECRET}" if WEBHOOK_SECRET else base


async def _register_waha_webhook() -> None:
    """Ensure the WAHA session has the backend webhook configured.

    Retries up to 5 times with 5s delay — WAHA may still be starting when
    the backend comes up, especially after a full stack restart.
    """
    headers = {"Content-Type": "application/json"}
    if WAHA_API_KEY:
        headers["X-Api-Key"] = WAHA_API_KEY
    payload = {"config": {"webhooks": [{"url": _webhook_url(), "events": WEBHOOK_EVENTS}]}}

    for attempt in range(1, 6):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.put(
                    f"{WAHA_BASE_URL}/api/sessions/{WAHA_SESSION}",
                    json=payload,
                    headers=headers,
                )
            if r.status_code in (200, 201):
                logger.info("WAHA webhook registered: %s → events=%s", _webhook_url(), WEBHOOK_EVENTS)
                return
            logger.warning("WAHA webhook registration failed (attempt %d/5): %s %s", attempt, r.status_code, r.text)
        except Exception:
            logger.warning("WAHA not reachable (attempt %d/5), retrying in 5s...", attempt)
        await asyncio.sleep(5)

    logger.error("Could not register WAHA webhook after 5 attempts — messages will not arrive")


def _log_security_warnings() -> None:
    """Emit startup warnings for insecure configuration."""
    from app.config import DB_ENCRYPTION_KEY
    if not WEBHOOK_SECRET:
        logger.warning("SECURITY: WEBHOOK_SECRET is not set — webhook endpoint is unauthenticated")
    if not TEST_TOKEN:
        logger.warning("SECURITY: TEST_TOKEN is not set — POST /test is open (disable or set token in prod)")
    if not DB_ENCRYPTION_KEY:
        logger.warning("SECURITY: DB_ENCRYPTION_KEY is not set — Google tokens stored in plaintext")
    if not WAHA_API_KEY:
        logger.warning("SECURITY: WAHA_API_KEY is not set — WAHA dashboard has no API authentication")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log_security_warnings()
    try:
        init_memory_tables()
    except Exception:
        logger.exception("Failed to initialise memory tables — aborting startup")
        raise
    try:
        await setup_checkpointer()
    except Exception:
        logger.exception("Failed to initialise postgres checkpointer — aborting startup")
        raise
    await _register_waha_webhook()
    from app.whatsapp import detect_own_lid
    await detect_own_lid()
    yield


app = FastAPI(title="PA Backend", version="0.2.0", lifespan=lifespan)

# CORS — allow all origins for this personal deployment (auth enforced per endpoint)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Test-Token"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.include_router(waha_router)
app.include_router(google_auth_router)
app.include_router(web_chat_router)

# Serve the web UI static files
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(os.path.join(_static_dir, "index.html"))

    @app.get("/manifest.json")
    async def serve_manifest():
        return FileResponse(os.path.join(_static_dir, "manifest.json"), media_type="application/manifest+json")


class TestRequest(BaseModel):
    text: str


@app.post("/test")
@limiter.limit("10/minute")
async def test_graph(
    request: Request,
    req: TestRequest,
    x_test_token: str = Header(default=""),
):
    """Dev endpoint: run text through LangGraph without WhatsApp.
    Requires X-Test-Token header matching TEST_TOKEN env var."""
    if not TEST_TOKEN or x_test_token != TEST_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Test-Token header")
    from app.graph.graph import run_graph
    reply = await run_graph(req.text, "test")
    return {"input": req.text, "reply": reply}


@app.get("/health")
@limiter.limit("60/minute")
async def health(request: Request):
    checks = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            checks["ollama"] = "ok" if r.status_code == 200 else "error"
        except Exception:
            checks["ollama"] = "unreachable"
        try:
            waha_headers = {"X-Api-Key": WAHA_API_KEY} if WAHA_API_KEY else {}
            r = await client.get(f"{WAHA_BASE_URL}/api/server/status", headers=waha_headers)
            checks["waha"] = "ok" if r.status_code == 200 else "error"
        except Exception:
            checks["waha"] = "unreachable"
    try:
        conn = _get_conn()
        conn.close()
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "error"
    all_ok = checks["ollama"] == "ok" and checks["postgres"] == "ok"
    return {"status": "ok" if all_ok else "degraded", "checks": checks}

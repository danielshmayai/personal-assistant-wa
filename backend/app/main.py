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
    WEBHOOK_SECRET, TEST_TOKEN, ALLOWED_ORIGIN,
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
    GEMINI_API_KEY, DATABASE_URL, MY_WHATSAPP_ID,
)
from app.whatsapp import router as waha_router
from app.memory.store import init_memory_tables, _get_conn
from app.routers.google_auth import router as google_auth_router
from app.routers.web_chat import router as web_chat_router
from app.routers.leads import router as leads_router
from app.routers.smart_home import router as smart_home_router
from app.routers.dashboard import router as dashboard_router
from app.routers.memory_api import router as memory_api_router
from app.graph.checkpointer import setup_checkpointer

from app.logging_config import setup_logging
from app.config import LOG_FORMAT
setup_logging(json_format=(LOG_FORMAT == "json"))
logger = logging.getLogger("pa.main")

WEBHOOK_EVENTS = ["message", "message.any"]

# IP-based rate limiter for public HTTP endpoints
limiter = Limiter(key_func=get_remote_address)


def _webhook_url() -> str:
    """Embed the shared secret in the callback URL so WAHA sends it on every request."""
    base = "http://backend:8000/webhook/waha"
    return f"{base}?secret={WEBHOOK_SECRET}" if WEBHOOK_SECRET else base


async def _waha_webhook_already_configured(client: httpx.AsyncClient, headers: dict) -> bool:
    """Return True if WAHA already has the correct webhook URL and events configured.

    Avoids an unnecessary PUT that restarts the WhatsApp session on every backend restart.
    """
    try:
        r = await client.get(f"{WAHA_BASE_URL}/api/sessions/{WAHA_SESSION}", headers=headers)
        if r.status_code != 200:
            return False
        data = r.json()
        configured = {
            w.get("url"): set(w.get("events", []))
            for w in (data.get("config") or {}).get("webhooks", [])
        }
        want_url = _webhook_url()
        want_events = set(WEBHOOK_EVENTS)
        return configured.get(want_url) == want_events
    except Exception:
        return False


async def _register_waha_webhook() -> None:
    """Ensure the WAHA session has the backend webhook configured.

    Checks the current config first and skips the PUT if the webhook is already
    correct — a PUT restarts the WhatsApp session which delays notifications.
    Retries up to 5 times with 5s delay for the case where WAHA is still starting.
    """
    headers = {"Content-Type": "application/json"}
    if WAHA_API_KEY:
        headers["X-Api-Key"] = WAHA_API_KEY
    payload = {"config": {"webhooks": [{"url": _webhook_url(), "events": WEBHOOK_EVENTS}]}}

    for attempt in range(1, 6):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                if await _waha_webhook_already_configured(client, headers):
                    logger.info("WAHA webhook already configured correctly — skipping PUT")
                    return
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


def _setup_langsmith() -> None:
    """Configure LangChain env vars for LangSmith tracing when API key is present."""
    import os
    from app.config import LANGSMITH_API_KEY, LANGSMITH_PROJECT
    if not LANGSMITH_API_KEY:
        return
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", LANGSMITH_API_KEY)
    os.environ.setdefault("LANGCHAIN_PROJECT", LANGSMITH_PROJECT)
    logger.info(
        "LangSmith tracing enabled project=%s",
        LANGSMITH_PROJECT,
        extra={"event": "langsmith_enabled", "project": LANGSMITH_PROJECT},
    )


def _check_secret(name: str, value: str, reason: str, missing: list[str]) -> None:
    if not value:
        logger.warning("SECURITY: %s is not set — %s", name, reason)
        missing.append(name)


def _log_security_warnings() -> None:
    """Emit startup warnings for insecure configuration; hard-fail in production."""
    from app.config import DB_ENCRYPTION_KEY, ENVIRONMENT
    missing_critical: list[str] = []

    _check_secret("WEBHOOK_SECRET", WEBHOOK_SECRET, "webhook endpoint is unauthenticated", missing_critical)
    _check_secret("DB_ENCRYPTION_KEY", DB_ENCRYPTION_KEY, "Google tokens stored in plaintext", missing_critical)
    _check_secret("GEMINI_API_KEY", GEMINI_API_KEY, "LLM calls will fail", missing_critical)
    _check_secret("DATABASE_URL", DATABASE_URL, "no database connection", missing_critical)
    _check_secret("MY_WHATSAPP_ID", MY_WHATSAPP_ID, "WhatsApp message routing broken", missing_critical)

    if not TEST_TOKEN:
        logger.warning("SECURITY: TEST_TOKEN is not set — admin/test endpoints open to anyone")
    if not WAHA_API_KEY:
        logger.warning("SECURITY: WAHA_API_KEY is not set — WAHA dashboard has no API authentication")
    if not ALLOWED_ORIGIN:
        logger.warning(
            "SECURITY: ALLOWED_ORIGIN is not set — CORS falls back to localhost only. "
            "Set ALLOWED_ORIGIN=https://<your-tunnel-domain> in production."
        )
    if not GOOGLE_CLIENT_ID:
        logger.warning(
            "CONFIG: GOOGLE_CLIENT_ID is not set — Google OAuth will fail with 'invalid_client'. "
            "Set GOOGLE_CLIENT_ID in .env (from Google Cloud Console → OAuth 2.0 Clients)."
        )
    if not GOOGLE_CLIENT_SECRET:
        logger.warning(
            "CONFIG: GOOGLE_CLIENT_SECRET is not set — Google OAuth token exchange will fail. "
            "Set GOOGLE_CLIENT_SECRET in .env (from Google Cloud Console → OAuth 2.0 Clients)."
        )

    if ENVIRONMENT == "production" and missing_critical:
        raise RuntimeError(
            f"Refusing to start in production — missing required secrets: "
            f"{', '.join(missing_critical)}"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_langsmith()
    _log_security_warnings()
    try:
        init_memory_tables()
    except Exception:
        logger.exception("Failed to initialise memory tables — aborting startup")
        raise
    try:
        from app.scheduled_jobs import init_table as init_jobs_table
        init_jobs_table()
    except Exception:
        logger.exception("Failed to initialise scheduled_jobs tables")
    try:
        await setup_checkpointer()
    except Exception:
        logger.exception("Failed to initialise postgres checkpointer — aborting startup")
        raise
    await _register_waha_webhook()
    from app.whatsapp import detect_own_lid
    await detect_own_lid()
    from app.memory.capabilities import sync_capabilities
    sync_capabilities()
    from app.worker import start_worker, stop_worker, recover_jobs
    start_worker()
    await recover_jobs()
    from app.scheduled_jobs import start as start_scheduler, stop as stop_scheduler
    await start_scheduler()
    yield
    await stop_scheduler()
    await stop_worker()


app = FastAPI(title="PA Backend", version="0.2.0", lifespan=lifespan)

# CORS — restricted to the configured tunnel domain (or localhost for dev).
# Set ALLOWED_ORIGIN=https://<tunnel-domain> in production.
_cors_origins = (
    [ALLOWED_ORIGIN]
    if ALLOWED_ORIGIN
    else ["http://localhost:3000", "http://localhost:8000", "http://127.0.0.1:8000"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Test-Token"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.include_router(waha_router)
app.include_router(google_auth_router)
app.include_router(web_chat_router)
app.include_router(leads_router)
app.include_router(smart_home_router)
app.include_router(dashboard_router)
app.include_router(memory_api_router)

# Serve the web UI static files
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(
            os.path.join(_static_dir, "index.html"),
            headers={"Cache-Control": "no-cache, must-revalidate", "Pragma": "no-cache"},
        )

    @app.get("/manifest.json")
    async def serve_manifest():
        return FileResponse(os.path.join(_static_dir, "manifest.json"), media_type="application/manifest+json")

    @app.get("/sw.js")
    async def serve_sw():
        # Service workers must be served with no-cache so the browser always
        # checks for a byte-change, which is how update detection works.
        return FileResponse(
            os.path.join(_static_dir, "sw.js"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache, must-revalidate", "Pragma": "no-cache"},
        )


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


@app.post("/admin/self-review")
@limiter.limit("5/minute")
async def trigger_self_review(
    request: Request,
    hours: int = 24,
    x_test_token: str = Header(default=""),
):
    """Trigger a self-review of the last N hours of conversations. Requires X-Test-Token."""
    if not TEST_TOKEN or x_test_token != TEST_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Test-Token header")
    from app.memory.self_review import run_self_review
    result = await run_self_review(hours=hours)
    return {"result": result}


@app.get("/health")
@limiter.limit("60/minute")
async def health(request: Request):
    from app.worker import worker_status
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
    checks["worker"] = worker_status()
    all_ok = checks["ollama"] == "ok" and checks["postgres"] == "ok"
    return {"status": "ok" if all_ok else "degraded", "checks": checks}

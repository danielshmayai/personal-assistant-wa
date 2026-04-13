import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
import httpx
from app.config import OLLAMA_BASE_URL, WAHA_BASE_URL, WAHA_API_KEY, WAHA_SESSION
from app.whatsapp import router as waha_router
from app.memory.store import init_memory_tables, _get_conn
from app.routers.google_auth import router as google_auth_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("pa.main")


WEBHOOK_URL = "http://backend:8000/webhook/waha"
WEBHOOK_EVENTS = ["message", "message.any"]


async def _register_waha_webhook() -> None:
    """Ensure the WAHA session has the backend webhook configured.

    Retries up to 5 times with 5s delay — WAHA may still be starting when
    the backend comes up, especially after a full stack restart.
    """
    headers = {"Content-Type": "application/json"}
    if WAHA_API_KEY:
        headers["X-Api-Key"] = WAHA_API_KEY
    payload = {"config": {"webhooks": [{"url": WEBHOOK_URL, "events": WEBHOOK_EVENTS}]}}

    for attempt in range(1, 6):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.put(
                    f"{WAHA_BASE_URL}/api/sessions/{WAHA_SESSION}",
                    json=payload,
                    headers=headers,
                )
            if r.status_code in (200, 201):
                logger.info("WAHA webhook registered: %s → events=%s", WEBHOOK_URL, WEBHOOK_EVENTS)
                return
            logger.warning("WAHA webhook registration failed (attempt %d/5): %s %s", attempt, r.status_code, r.text)
        except Exception:
            logger.warning("WAHA not reachable (attempt %d/5), retrying in 5s...", attempt)
        await asyncio.sleep(5)

    logger.error("Could not register WAHA webhook after 5 attempts — messages will not arrive")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_memory_tables()
    except Exception:
        logger.exception("Failed to initialise memory tables — aborting startup")
        raise
    await _register_waha_webhook()
    yield


app = FastAPI(title="PA Backend", version="0.2.0", lifespan=lifespan)
app.include_router(waha_router)
app.include_router(google_auth_router)


class TestRequest(BaseModel):
    text: str


@app.post("/test")
async def test_graph(req: TestRequest):
    """Dev-only endpoint: send text directly to the LangGraph pipeline (no WhatsApp needed)."""
    from app.graph.graph import run_graph
    reply = await run_graph(req.text, "test")
    return {"input": req.text, "reply": reply}


@app.get("/health")
async def health():
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

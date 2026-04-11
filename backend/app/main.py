import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
import httpx
from app.config import OLLAMA_BASE_URL, WAHA_BASE_URL, DATABASE_URL
from app.whatsapp import router as waha_router
from app.memory.store import init_memory_tables

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_memory_tables()
    yield


app = FastAPI(title="PA Backend", version="0.2.0", lifespan=lifespan)
app.include_router(waha_router)


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
            r = await client.get(f"{WAHA_BASE_URL}/api/server/status")
            checks["waha"] = "ok" if r.status_code == 200 else "error"
        except Exception:
            checks["waha"] = "unreachable"
    checks["postgres"] = "configured" if DATABASE_URL else "missing"
    all_ok = checks["ollama"] == "ok" and checks["waha"] == "ok"
    return {"status": "ok" if all_ok else "degraded", "checks": checks}

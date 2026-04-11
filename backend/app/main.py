from fastapi import FastAPI
import httpx
import os

app = FastAPI(title="PA Backend", version="0.1.0")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
WAHA_BASE_URL = os.getenv("WAHA_BASE_URL", "http://waha:3000")
DATABASE_URL = os.getenv("DATABASE_URL", "")


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

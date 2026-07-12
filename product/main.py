"""Product entrypoint: multi-tenant web assistant wrapping the PA engine.

Run: uvicorn product.main:app

Deliberately does NOT do what the owner entrypoint (app/main.py) does:
no WAHA webhook registration, no WhatsApp worker, no nightly restart loop,
no proactive flows / scheduler. Web chat streams directly per connection.
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import runtime
from app.context import current_tenant_id
from app.logging_config import setup_logging
from product.config import ENVIRONMENT, VAULT_BASE_DIR, require_platform_config

setup_logging(json_format=os.getenv("LOG_FORMAT", "json") == "json")
logger = logging.getLogger("product.main")


def _wire_engine_seam() -> None:
    """Inject tenant-aware resolvers into the engine."""
    from product.secrets import get_secrets_backend
    from product.storage import get_vault_storage

    secrets_backend = get_secrets_backend()
    vault_storage = get_vault_storage()
    owner_vault = Path(os.getenv("OBSIDIAN_VAULT_PATH", f"{VAULT_BASE_DIR}/owner"))

    def resolve_secret(key: str) -> str | None:
        tid = current_tenant_id.get()
        if tid:
            # Strict tenant isolation: BYO keys only, no env fallback —
            # a tenant must never silently run on the platform's keys.
            return secrets_backend.get(tid, key)
        return os.getenv(key)  # owner scope keeps env behaviour

    def resolve_vault_root() -> Path:
        tid = current_tenant_id.get()
        if tid:
            return vault_storage.root_for(tid)
        owner_vault.mkdir(parents=True, exist_ok=True)
        return owner_vault

    runtime.set_secret_resolver(resolve_secret)
    runtime.set_vault_root_resolver(resolve_vault_root)


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = require_platform_config()
    if missing:
        message = f"Missing required platform config: {', '.join(missing)}"
        if ENVIRONMENT == "production":
            raise RuntimeError(message)
        logger.warning("%s — running in degraded dev mode", message)

    _wire_engine_seam()

    from product.db import run_migrations
    run_migrations()

    from app.memory.store import init_memory_tables
    init_memory_tables()

    # Module data tables (tenant-scoped via current_tenant_id). Safe + idempotent
    # now that the destructive "collapse to default" migrations were removed.
    from app.nutrition import init_table as init_nutrition_table
    init_nutrition_table()
    from app.fitness import init_table as init_fitness_table
    init_fitness_table()
    from app.garmin.client import init_table as init_garmin_table
    init_garmin_table()
    from app.push_notifications import init_push_table
    init_push_table()

    from app.graph.checkpointer import setup_checkpointer
    await setup_checkpointer()

    # Tenant-scoped scheduler instance: polls only tenant_id != '' jobs, so
    # it never races with the owner's own scheduler running in the separate
    # `backend` process/container against the same table (see
    # scheduled_jobs.get_due_jobs's docstring).
    from app.scheduled_jobs import start as start_scheduler, stop as stop_scheduler
    await start_scheduler(tenant_scope=True)

    logger.info("Product backend ready (env=%s)", ENVIRONMENT)
    yield
    await stop_scheduler()


app = FastAPI(title="PA Product", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    from product.db import get_conn
    db_ok = True
    try:
        conn = get_conn()
        conn.close()
    except Exception:
        db_ok = False
    status = 200 if db_ok else 503
    return JSONResponse({"ok": db_ok, "services": {"postgres": db_ok}}, status_code=status)


from product.routers import (  # noqa: E402
    admin, auth, chat, dashboard, drive_proxy, fitness, garmin, google_connect, me, memory,
    modules, nutrition, onboarding, push, secrets, smart_home, voice,
)

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(onboarding.router)
app.include_router(secrets.router)
app.include_router(modules.router)
app.include_router(google_connect.router)
app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(nutrition.router)
app.include_router(fitness.router)
app.include_router(garmin.router)
app.include_router(dashboard.router)
app.include_router(smart_home.router)
app.include_router(memory.router)
app.include_router(drive_proxy.router)
app.include_router(voice.router)
app.include_router(push.router)

# Optional single-container mode: serve the built SPA if present
_SPA_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _SPA_DIST.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(_SPA_DIST), html=True), name="spa")

"""Scheduled jobs — DB helpers + background execution loop.

Table: scheduled_jobs
  id, chat_id, action_type, payload (JSONB), run_at, status, error, created_at,
  recurrence (TEXT: null | daily | weekly | hourly | minutes:N)

Action types:
  tuya_command  — payload: {device_id, commands, description}
  send_message  — payload: {text}
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import psycopg2

from app.config import DATABASE_URL

logger = logging.getLogger("pa.scheduler")

# ── DB init ───────────────────────────────────────────────────────────────────

def init_table() -> None:
    if not DATABASE_URL:
        return
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    id          SERIAL PRIMARY KEY,
                    chat_id     TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    payload     JSONB NOT NULL,
                    run_at      TIMESTAMPTZ NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'pending',
                    error       TEXT,
                    created_at  TIMESTAMPTZ DEFAULT NOW(),
                    executed_at TIMESTAMPTZ,
                    recurrence  TEXT
                )
            """)
            cur.execute(
                "ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS recurrence TEXT"
            )
            # Explicit scope column so the scheduler can query "mine only" per
            # process (owner's backend vs product-api) with a plain WHERE —
            # no guessing tenant-vs-owner from the chat_id string shape, and no
            # race between two processes independently polling the same table.
            cur.execute(
                "ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_run_at "
                "ON scheduled_jobs (run_at) WHERE status = 'pending'"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_tenant "
                "ON scheduled_jobs (tenant_id) WHERE status = 'pending'"
            )
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pending_notifications (
                    id          SERIAL PRIMARY KEY,
                    chat_id     TEXT NOT NULL,
                    message     TEXT NOT NULL,
                    created_at  TIMESTAMPTZ DEFAULT NOW(),
                    delivered_at TIMESTAMPTZ
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_pending_notif_chat "
                "ON pending_notifications (chat_id) WHERE delivered_at IS NULL"
            )
            cur.execute("""
                CREATE TABLE IF NOT EXISTS activity_log (
                    id          SERIAL PRIMARY KEY,
                    chat_id     TEXT NOT NULL,
                    event_type  TEXT NOT NULL,
                    summary     TEXT NOT NULL,
                    detail      JSONB,
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_activity_log_chat "
                "ON activity_log (chat_id, created_at DESC)"
            )
            cur.execute("""
                CREATE TABLE IF NOT EXISTS proactive_cards (
                    id           SERIAL PRIMARY KEY,
                    chat_id      TEXT NOT NULL,
                    card_type    TEXT NOT NULL,
                    title        TEXT NOT NULL,
                    detail       TEXT,
                    action_label TEXT,
                    action_chat  TEXT,
                    expires_at   TIMESTAMPTZ,
                    created_at   TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_proactive_cards_chat "
                "ON proactive_cards (chat_id, created_at DESC)"
            )
        conn.commit()
    finally:
        conn.close()


def store_notification(chat_id: str, message: str) -> None:
    if not DATABASE_URL:
        return
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pending_notifications (chat_id, message) VALUES (%s, %s)",
                (chat_id, message),
            )
        conn.commit()
    finally:
        conn.close()


def fetch_pending_notifications(chat_id: str) -> list[str]:
    if not DATABASE_URL:
        return []
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message FROM pending_notifications "
                "WHERE chat_id = %s AND delivered_at IS NULL ORDER BY created_at",
                (chat_id,),
            )
            rows = cur.fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def mark_notifications_delivered(chat_id: str) -> None:
    if not DATABASE_URL:
        return
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM pending_notifications WHERE chat_id = %s AND delivered_at IS NULL",
                (chat_id,),
            )
        conn.commit()
    finally:
        conn.close()


# ── DB CRUD ───────────────────────────────────────────────────────────────────

def _scoped_chat_id(chat_id: str) -> str:
    """Tenant scope always overrides to a stable per-tenant key, matching the
    convention product/routers/dashboard.py and push.py already use
    (f"web_{engine_scope}"). Chat tools only have the current conversation's
    ephemeral thread id (a fresh uuid per WS connect) — without this, a
    reminder created in one chat session would be stored/looked-up under an
    id no other session (or the scheduler's notification/push delivery,
    which uses the stable key) will ever see again. Owner scope passes
    chat_id through unchanged — zero behavior change for WhatsApp/PWA."""
    from app.context import current_tenant_id
    tid = current_tenant_id.get()
    return f"web_{tid}" if tid else chat_id


def insert_job(
    chat_id: str,
    action_type: str,
    payload: dict,
    run_at: datetime,
    recurrence: str | None = None,
) -> int:
    from app.context import current_tenant_id

    tenant_id = current_tenant_id.get()
    chat_id = _scoped_chat_id(chat_id)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scheduled_jobs (chat_id, tenant_id, action_type, payload, run_at, recurrence)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                RETURNING id
                """,
                (chat_id, tenant_id, action_type, json.dumps(payload), run_at, recurrence),
            )
            job_id = cur.fetchone()[0]
        conn.commit()
        return job_id
    finally:
        conn.close()


def get_due_jobs(tenant_scope: bool = False) -> list[dict]:
    """tenant_scope=False (owner's backend process): only tenant_id='' jobs.
    tenant_scope=True (product-api process): only tenant_id!='' jobs.

    The owner's engine and the product run as separate processes, each
    polling this same table independently — without this split, both would
    see and execute the same due job (double Tuya command fire, duplicate
    reminder). Splitting by tenant_id (set once, correctly, by insert_job)
    keeps the two processes' queries disjoint with no cross-process locking.
    """
    if not DATABASE_URL:
        return []
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, chat_id, tenant_id, action_type, payload, run_at, recurrence
                FROM scheduled_jobs
                WHERE status = 'pending' AND run_at <= NOW()
                  AND tenant_id {'!=' if tenant_scope else '='} ''
                ORDER BY run_at
                """,
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0], "chat_id": r[1], "tenant_id": r[2], "action_type": r[3],
                "payload": r[4], "run_at": r[5], "recurrence": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()


def _next_run(run_at: datetime, recurrence: str) -> datetime | None:
    """Compute next fire time for a recurring job. Returns None for unknown patterns."""
    from zoneinfo import ZoneInfo
    from app.config import USER_TIMEZONE
    tz = ZoneInfo(USER_TIMEZONE)
    local = run_at.astimezone(tz)
    if recurrence == "daily":
        return (local + timedelta(days=1)).astimezone(timezone.utc)
    if recurrence == "weekly":
        return (local + timedelta(weeks=1)).astimezone(timezone.utc)
    if recurrence == "hourly":
        return (local + timedelta(hours=1)).astimezone(timezone.utc)
    if recurrence.startswith("minutes:"):
        try:
            mins = int(recurrence.split(":")[1])
            return (local + timedelta(minutes=mins)).astimezone(timezone.utc)
        except (IndexError, ValueError):
            pass
    return None


def _set_status(job_id: int, status: str, error: str | None = None) -> None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scheduled_jobs
                SET status = %s, error = %s, executed_at = NOW()
                WHERE id = %s
                """,
                (status, error, job_id),
            )
        conn.commit()
    finally:
        conn.close()


def mark_job_done(job_id: int) -> None:
    _set_status(job_id, "done")


def mark_job_failed(job_id: int, error: str) -> None:
    _set_status(job_id, "failed", error)


def list_pending_jobs(chat_id: str) -> list[dict]:
    if not DATABASE_URL:
        return []
    chat_id = _scoped_chat_id(chat_id)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, action_type, payload, run_at, recurrence
                FROM scheduled_jobs
                WHERE chat_id = %s AND status = 'pending'
                ORDER BY run_at
                """,
                (chat_id,),
            )
            rows = cur.fetchall()
        return [
            {"id": r[0], "action_type": r[1], "payload": r[2], "run_at": r[3], "recurrence": r[4]}
            for r in rows
        ]
    finally:
        conn.close()


def cancel_job(job_id: int, chat_id: str) -> bool:
    """Cancel a pending job. Returns True if a row was updated."""
    chat_id = _scoped_chat_id(chat_id)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scheduled_jobs
                SET status = 'cancelled', executed_at = NOW()
                WHERE id = %s AND chat_id = %s AND status = 'pending'
                """,
                (job_id, chat_id),
            )
            affected = cur.rowcount
        conn.commit()
        return affected > 0
    finally:
        conn.close()


# ── Activity log ─────────────────────────────────────────────────────────────

def log_activity(chat_id: str, event_type: str, summary: str, detail: dict | None = None) -> None:
    if not DATABASE_URL:
        return
    chat_id = _scoped_chat_id(chat_id)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO activity_log (chat_id, event_type, summary, detail) VALUES (%s, %s, %s, %s::jsonb)",
                (chat_id, event_type, summary, json.dumps(detail) if detail else None),
            )
        conn.commit()
    except Exception:
        logger.exception("log_activity failed")
    finally:
        conn.close()


def get_activity(chat_id: str, limit: int = 50, event_type: str | None = None) -> list[dict]:
    if not DATABASE_URL:
        return []
    chat_id = _scoped_chat_id(chat_id)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            if event_type:
                cur.execute(
                    "SELECT id, event_type, summary, detail, created_at FROM activity_log "
                    "WHERE chat_id = %s AND event_type = %s ORDER BY created_at DESC LIMIT %s",
                    (chat_id, event_type, limit),
                )
            else:
                cur.execute(
                    "SELECT id, event_type, summary, detail, created_at FROM activity_log "
                    "WHERE chat_id = %s ORDER BY created_at DESC LIMIT %s",
                    (chat_id, limit),
                )
            rows = cur.fetchall()
        return [
            {"id": r[0], "event_type": r[1], "summary": r[2], "detail": r[3], "created_at": r[4].isoformat()}
            for r in rows
        ]
    finally:
        conn.close()


# ── Proactive cards ───────────────────────────────────────────────────────────

def upsert_card(chat_id: str, card_type: str, title: str, detail: str = "",
                action_label: str = "", action_chat: str = "",
                expires_at: datetime | None = None) -> int:
    if not DATABASE_URL:
        return -1
    chat_id = _scoped_chat_id(chat_id)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO proactive_cards
                   (chat_id, card_type, title, detail, action_label, action_chat, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (chat_id, card_type, title, detail, action_label, action_chat, expires_at),
            )
            card_id = cur.fetchone()[0]
        conn.commit()
        return card_id
    finally:
        conn.close()


def get_active_cards(chat_id: str) -> list[dict]:
    if not DATABASE_URL:
        return []
    chat_id = _scoped_chat_id(chat_id)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, card_type, title, detail, action_label, action_chat, expires_at, created_at
                   FROM proactive_cards
                   WHERE chat_id = %s AND (expires_at IS NULL OR expires_at > NOW())
                   ORDER BY card_type, created_at DESC""",
                (chat_id,),
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0], "card_type": r[1], "title": r[2], "detail": r[3],
                "action_label": r[4], "action_chat": r[5],
                "expires_at": r[6].isoformat() if r[6] else None,
                "created_at": r[7].isoformat(),
            }
            for r in rows
        ]
    finally:
        conn.close()


def delete_card(card_id: int, chat_id: str) -> bool:
    if not DATABASE_URL:
        return False
    chat_id = _scoped_chat_id(chat_id)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM proactive_cards WHERE id = %s AND chat_id = %s",
                (card_id, chat_id),
            )
            affected = cur.rowcount
        conn.commit()
        return affected > 0
    finally:
        conn.close()


def list_all_jobs(chat_id: str, include_done: bool = False) -> list[dict]:
    """Return all jobs for a chat_id, optionally including done/failed/cancelled."""
    if not DATABASE_URL:
        return []
    chat_id = _scoped_chat_id(chat_id)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            if include_done:
                cur.execute(
                    """SELECT id, action_type, payload, run_at, status, executed_at
                       FROM scheduled_jobs WHERE chat_id = %s
                       ORDER BY run_at DESC LIMIT 100""",
                    (chat_id,),
                )
            else:
                cur.execute(
                    """SELECT id, action_type, payload, run_at, status, executed_at
                       FROM scheduled_jobs WHERE chat_id = %s AND status = 'pending'
                       ORDER BY run_at ASC""",
                    (chat_id,),
                )
            rows = cur.fetchall()
        return [
            {
                "id": r[0], "action_type": r[1], "payload": r[2],
                "run_at": r[3].isoformat(), "status": r[4],
                "executed_at": r[5].isoformat() if r[5] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()


# ── Execution ─────────────────────────────────────────────────────────────────

async def _run_job(job: dict) -> str:
    atype = job["action_type"]
    payload = job["payload"]

    if atype == "tuya_command":
        from app.config import TUYA_PREFER_LOCAL
        from app.tuya.tools import _send_command_local, _send_command_cloud
        device_id = payload["device_id"]
        commands = payload["commands"]
        result = None
        if TUYA_PREFER_LOCAL:
            try:
                result = await asyncio.to_thread(_send_command_local, device_id, commands)
            except Exception:
                pass
        if result is None:
            result = await asyncio.to_thread(_send_command_cloud, device_id, commands)
        desc = payload.get("description", "פקודת בית חכם")
        success = (result or {}).get("success", True)
        status = "✅" if success else "⚠️"
        return f"{status} פעולה מתוזמנת בוצעה: {desc}"

    if atype == "send_message":
        return payload.get("text", "תזכורת!")

    raise ValueError(f"Unknown action_type: {atype!r}")


async def _notify(chat_id: str, text: str) -> None:
    from app.broadcast import NotificationManager

    active = NotificationManager.active_web_sessions()
    logger.info("_notify: chat_id=%r active_ws=%s", chat_id, active)

    if chat_id.startswith("web"):
        try:
            await asyncio.to_thread(store_notification, chat_id, text)
        except Exception as exc:
            logger.error("_notify: store_notification failed: %s", exc)

    wa_ids = [] if chat_id.startswith("web") else [chat_id]
    web_id = chat_id if chat_id.startswith("web") else None
    await NotificationManager.broadcast(
        message=text,
        whatsapp_chat_ids=wa_ids or None,
        web_chat_id=web_id,
    )

    # Web Push — always attempt so the OS shows a notification even when the
    # tab is open in the background. The service worker suppresses the OS pop-up
    # when the page is already focused.
    if chat_id.startswith("web"):
        try:
            from app.push_notifications import get_push_subscriptions, send_web_push_sync
            subs = await asyncio.to_thread(get_push_subscriptions, chat_id)
            if subs:
                await asyncio.gather(
                    *[
                        asyncio.to_thread(send_web_push_sync, sub, "danidin 🔔", text)
                        for sub in subs
                    ],
                    return_exceptions=True,
                )
        except Exception as exc:
            logger.warning("_notify: web push failed: %s", exc)


async def _execute_due_jobs(tenant_scope: bool = False) -> None:
    from app.context import current_tenant_id

    jobs = await asyncio.to_thread(get_due_jobs, tenant_scope)
    for job in jobs:
        jid = job["id"]
        # Scope every step of this job's execution to its owning tenant.
        # Without this, _run_job's tuya_command path would resolve Tuya
        # credentials via the ContextVar-unset (owner) default instead of
        # the tenant's own BYO creds — same bug class as the P4 Garmin fix.
        token = current_tenant_id.set(job["tenant_id"])
        try:
            # Mark running immediately so concurrent ticks don't double-execute
            await asyncio.to_thread(_set_status, jid, "running")
            try:
                msg = await _run_job(job)
                await asyncio.to_thread(mark_job_done, jid)
                logger.info("Job %d done: %s", jid, msg)
                await asyncio.to_thread(
                    log_activity, job["chat_id"], "job",
                    msg, {"job_id": jid, "action_type": job["action_type"]}
                )
                # Re-schedule if recurring
                recurrence = job.get("recurrence")
                if recurrence:
                    next_run = _next_run(job["run_at"], recurrence)
                    if next_run:
                        new_id = await asyncio.to_thread(
                            insert_job,
                            job["chat_id"], job["action_type"], job["payload"],
                            next_run, recurrence,
                        )
                        logger.info("Recurring job %d rescheduled → #%d at %s", jid, new_id, next_run)
                await _notify(job["chat_id"], msg)
            except Exception as exc:
                logger.exception("Job %d failed", jid)
                await asyncio.to_thread(mark_job_failed, jid, str(exc))
                await asyncio.to_thread(
                    log_activity, job["chat_id"], "job",
                    f"❌ Job {jid} failed: {exc}", {"job_id": jid, "error": str(exc)}
                )
                await _notify(job["chat_id"], f"❌ הפעולה המתוזמנת נכשלה: {exc}")
        finally:
            current_tenant_id.reset(token)


# ── Background loop ───────────────────────────────────────────────────────────

_task: asyncio.Task | None = None
_POLL_INTERVAL = 30  # seconds


async def start(tenant_scope: bool = False) -> None:
    """tenant_scope=False (default): owner's engine entrypoint, unchanged
    behavior. tenant_scope=True: product-api's own instance, polling only
    tenant-owned jobs — see get_due_jobs for why the split matters."""
    global _task
    _task = asyncio.create_task(_loop(tenant_scope), name="scheduler")
    logger.info("Scheduler started (poll every %ds, tenant_scope=%s)", _POLL_INTERVAL, tenant_scope)


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
    logger.info("Scheduler stopped")


async def _loop(tenant_scope: bool = False) -> None:
    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            await _execute_due_jobs(tenant_scope)
        except Exception:
            logger.exception("Scheduler tick error")

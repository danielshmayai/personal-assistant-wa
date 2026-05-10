"""Scheduled jobs — DB helpers + background execution loop.

Table: scheduled_jobs
  id, chat_id, action_type, payload (JSONB), run_at, status, error, created_at

Action types:
  tuya_command  — payload: {device_id, commands, description}
  send_message  — payload: {text}
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

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
                    executed_at TIMESTAMPTZ
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_run_at "
                "ON scheduled_jobs (run_at) WHERE status = 'pending'"
            )
        conn.commit()
    finally:
        conn.close()


# ── DB CRUD ───────────────────────────────────────────────────────────────────

def insert_job(chat_id: str, action_type: str, payload: dict, run_at: datetime) -> int:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scheduled_jobs (chat_id, action_type, payload, run_at)
                VALUES (%s, %s, %s::jsonb, %s)
                RETURNING id
                """,
                (chat_id, action_type, json.dumps(payload), run_at),
            )
            job_id = cur.fetchone()[0]
        conn.commit()
        return job_id
    finally:
        conn.close()


def get_due_jobs() -> list[dict]:
    if not DATABASE_URL:
        return []
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, chat_id, action_type, payload, run_at
                FROM scheduled_jobs
                WHERE status = 'pending' AND run_at <= NOW()
                ORDER BY run_at
                """,
            )
            rows = cur.fetchall()
        return [
            {"id": r[0], "chat_id": r[1], "action_type": r[2], "payload": r[3], "run_at": r[4]}
            for r in rows
        ]
    finally:
        conn.close()


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
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, action_type, payload, run_at
                FROM scheduled_jobs
                WHERE chat_id = %s AND status = 'pending'
                ORDER BY run_at
                """,
                (chat_id,),
            )
            rows = cur.fetchall()
        return [
            {"id": r[0], "action_type": r[1], "payload": r[2], "run_at": r[3]}
            for r in rows
        ]
    finally:
        conn.close()


def cancel_job(job_id: int, chat_id: str) -> bool:
    """Cancel a pending job. Returns True if a row was updated."""
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


# ── Execution ─────────────────────────────────────────────────────────────────

async def _run_job(job: dict) -> str:
    atype = job["action_type"]
    payload = job["payload"]

    if atype == "tuya_command":
        from app.tuya.tools import TUYA_PREFER_LOCAL
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
        return f"✅ ביצעתי: {desc}"

    if atype == "send_message":
        return payload.get("text", "תזכורת!")

    raise ValueError(f"Unknown action_type: {atype!r}")


async def _notify(chat_id: str, text: str) -> None:
    from app.broadcast import NotificationManager
    wa_ids = [] if chat_id.startswith("web") else [chat_id]
    web_id = chat_id if chat_id.startswith("web") else None
    await NotificationManager.broadcast(
        message=text,
        whatsapp_chat_ids=wa_ids or None,
        web_chat_id=web_id,
    )


async def _execute_due_jobs() -> None:
    jobs = await asyncio.to_thread(get_due_jobs)
    for job in jobs:
        jid = job["id"]
        # Mark running immediately so concurrent ticks don't double-execute
        await asyncio.to_thread(_set_status, jid, "running")
        try:
            msg = await _run_job(job)
            await asyncio.to_thread(mark_job_done, jid)
            logger.info("Job %d done: %s", jid, msg)
            await _notify(job["chat_id"], msg)
        except Exception as exc:
            logger.exception("Job %d failed", jid)
            await asyncio.to_thread(mark_job_failed, jid, str(exc))
            await _notify(job["chat_id"], f"❌ הפעולה המתוזמנת נכשלה: {exc}")


# ── Background loop ───────────────────────────────────────────────────────────

_task: asyncio.Task | None = None
_POLL_INTERVAL = 30  # seconds


async def start() -> None:
    global _task
    _task = asyncio.create_task(_loop(), name="scheduler")
    logger.info("Scheduler started (poll every %ds)", _POLL_INTERVAL)


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


async def _loop() -> None:
    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            await _execute_due_jobs()
        except Exception:
            logger.exception("Scheduler tick error")

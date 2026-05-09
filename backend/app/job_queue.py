import logging

import psycopg2

from app.config import DATABASE_URL

logger = logging.getLogger("pa.job_queue")

# Valid status values
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def _get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_table() -> None:
    """Create the job_queue table if it doesn't exist. Idempotent."""
    if not DATABASE_URL:
        return
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS job_queue (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    message_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    full_text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    retry_count INT NOT NULL DEFAULT 0,
                    max_retries INT NOT NULL DEFAULT 4,
                    error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT job_queue_message_id_key UNIQUE (message_id)
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS job_queue_status_idx"
                " ON job_queue(status, created_at)"
            )
        conn.commit()
    finally:
        conn.close()


def persist_job(message_id: str, chat_id: str, full_text: str) -> bool:
    """Insert a new job row.

    Returns False when message_id already exists in the table (DB-level idempotency).
    Returns True on success or when DATABASE_URL is unset (fail-open so the
    in-memory dedup cache remains the safety net).
    """
    if not DATABASE_URL or not message_id:
        return True
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_queue (message_id, chat_id, full_text)
                VALUES (%s, %s, %s)
                ON CONFLICT ON CONSTRAINT job_queue_message_id_key DO NOTHING
                """,
                (message_id, chat_id, full_text),
            )
            inserted = cur.rowcount > 0
        conn.commit()
        return inserted
    except Exception:
        logger.exception("Failed to persist job message_id=%s", message_id)
        return True  # fail open
    finally:
        conn.close()


def update_job_status(
    message_id: str, status: str, error: str | None = None
) -> None:
    """Update a job's status.  Increments retry_count when status is 'failed'."""
    if not DATABASE_URL or not message_id:
        return
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if status == STATUS_FAILED:
                cur.execute(
                    """
                    UPDATE job_queue
                    SET status = %s, retry_count = retry_count + 1,
                        error = %s, updated_at = NOW()
                    WHERE message_id = %s
                    """,
                    (status, error, message_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE job_queue
                    SET status = %s, error = %s, updated_at = NOW()
                    WHERE message_id = %s
                    """,
                    (status, error, message_id),
                )
        conn.commit()
    except Exception:
        logger.debug(
            "Failed to update job status message_id=%s status=%s",
            message_id,
            status,
        )
    finally:
        conn.close()


def load_pending_jobs() -> list[dict]:
    """Return jobs in pending/processing state that have not exceeded max_retries.

    Used at startup to recover in-flight messages that survived a server restart.
    """
    if not DATABASE_URL:
        return []
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT message_id, chat_id, full_text
                FROM job_queue
                WHERE status IN ('pending', 'processing')
                  AND retry_count < max_retries
                ORDER BY created_at
                """
            )
            return [
                {"message_id": r[0], "chat_id": r[1], "full_text": r[2]}
                for r in cur.fetchall()
            ]
    except Exception:
        logger.exception("Failed to load pending jobs")
        return []
    finally:
        conn.close()

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from app.context import request_id_var

logger = logging.getLogger("pa.worker")

# In-process queue — single worker, no persistence across restarts.
_queue: asyncio.Queue = asyncio.Queue()

# L1 dedup cache: message_id → epoch timestamp when first seen.
# Prevents re-processing within the same process lifetime (fast path).
# DB constraint is the L2 guard for cross-restart idempotency.
_processed_ids: dict[str, float] = {}
_DEDUP_WINDOW_SECS = 3600  # prune entries older than 1 hour

# Retry: 4 total attempts — immediate, then after 1s, 5s, 15s.
_MAX_ATTEMPTS = 4
_RETRY_DELAYS = (1, 5, 15)

_worker_task: asyncio.Task | None = None


@dataclass
class _Msg:
    message_id: str
    chat_id: str
    full_text: str
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))


def _prune_dedup_cache() -> None:
    now = time.time()
    expired = [mid for mid, ts in list(_processed_ids.items()) if now - ts > _DEDUP_WINDOW_SECS]
    for mid in expired:
        del _processed_ids[mid]


def enqueue(message_id: str, chat_id: str, full_text: str) -> bool:
    """Add a message to the async processing queue.

    Returns False if message_id was already seen (in-memory L1 or DB L2 check).
    Logs a warning when queue depth reaches 10.
    """
    _prune_dedup_cache()

    # L1: in-memory dedup (fast path, same process lifetime)
    if message_id and message_id in _processed_ids:
        logger.info(
            "worker: duplicate message_id=%s (in-memory) — skipping",
            message_id,
            extra={"event": "dedup_hit", "message_id": message_id, "layer": "memory"},
        )
        return False

    # L2: DB persist + idempotency (cross-restart durability)
    if message_id:
        from app.job_queue import persist_job
        if not persist_job(message_id, chat_id, full_text):
            logger.info(
                "worker: duplicate message_id=%s (db) — skipping",
                message_id,
                extra={"event": "dedup_hit", "message_id": message_id, "layer": "db"},
            )
            return False
        _processed_ids[message_id] = time.time()

    depth = _queue.qsize()
    if depth >= 10:
        logger.warning(
            "worker: queue depth=%d >= 10 — processing may be lagging",
            depth,
            extra={"event": "queue_lag", "queue_depth": depth},
        )
    msg = _Msg(message_id=message_id, chat_id=chat_id, full_text=full_text)
    _queue.put_nowait(msg)
    logger.info(
        "worker: enqueued message_id=%s request_id=%s queue_depth=%d",
        message_id,
        msg.request_id,
        depth + 1,
        extra={
            "event": "enqueue",
            "message_id": message_id,
            "chat_id": chat_id,
            "request_id": msg.request_id,
            "queue_depth": depth + 1,
        },
    )
    return True


def queue_depth() -> int:
    return _queue.qsize()


def worker_status() -> dict:
    running = _worker_task is not None and not _worker_task.done()
    return {"running": running, "queue_depth": _queue.qsize()}


async def _process_one(msg: _Msg) -> None:
    # Deferred import to avoid circular dependency (worker ↔ whatsapp).
    from app.whatsapp import _process_message, send_whatsapp_message
    from app.job_queue import update_job_status, STATUS_PROCESSING, STATUS_DONE, STATUS_FAILED

    # Propagate request_id to all awaited code via ContextVar.
    token = request_id_var.set(msg.request_id)
    last_exc: Exception | None = None

    update_job_status(msg.message_id, STATUS_PROCESSING)

    try:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            if attempt > 1:
                await asyncio.sleep(_RETRY_DELAYS[attempt - 2])
                logger.warning(
                    "worker: retry attempt=%d/%d message_id=%s request_id=%s error=%s",
                    attempt,
                    _MAX_ATTEMPTS,
                    msg.message_id,
                    msg.request_id,
                    last_exc,
                    extra={
                        "event": "retry",
                        "attempt": attempt,
                        "max_attempts": _MAX_ATTEMPTS,
                        "message_id": msg.message_id,
                        "request_id": msg.request_id,
                        "error": str(last_exc),
                    },
                )
            try:
                reply = await _process_message(msg.full_text, msg.chat_id)
                await send_whatsapp_message(msg.chat_id, reply)
                update_job_status(msg.message_id, STATUS_DONE)
                logger.info(
                    "worker: ok message_id=%s request_id=%s attempt=%d",
                    msg.message_id,
                    msg.request_id,
                    attempt,
                    extra={
                        "event": "message_ok",
                        "message_id": msg.message_id,
                        "request_id": msg.request_id,
                        "chat_id": msg.chat_id,
                        "attempt": attempt,
                    },
                )
                return
            except Exception as exc:
                last_exc = exc
                update_job_status(msg.message_id, STATUS_FAILED, str(exc))
                logger.warning(
                    "worker: attempt %d/%d failed message_id=%s request_id=%s: %s",
                    attempt,
                    _MAX_ATTEMPTS,
                    msg.message_id,
                    msg.request_id,
                    exc,
                    extra={
                        "event": "attempt_failed",
                        "attempt": attempt,
                        "max_attempts": _MAX_ATTEMPTS,
                        "message_id": msg.message_id,
                        "request_id": msg.request_id,
                        "error": str(exc),
                    },
                )
    finally:
        request_id_var.reset(token)

    logger.error(
        "worker: giving up message_id=%s request_id=%s chat=%s after %d attempts",
        msg.message_id,
        msg.request_id,
        msg.chat_id,
        _MAX_ATTEMPTS,
        exc_info=last_exc,
        extra={
            "event": "give_up",
            "message_id": msg.message_id,
            "request_id": msg.request_id,
            "chat_id": msg.chat_id,
            "attempts": _MAX_ATTEMPTS,
        },
    )


async def recover_jobs() -> None:
    """Re-enqueue jobs that were pending or processing when the server last stopped.

    Called once at startup after start_worker(). Bypasses DB persist since the
    jobs already exist; only re-adds them to the in-memory asyncio.Queue.
    """
    try:
        from app.job_queue import load_pending_jobs, update_job_status, STATUS_PENDING
        loop = asyncio.get_running_loop()
        jobs = await loop.run_in_executor(None, load_pending_jobs)
    except Exception:
        logger.warning("worker: could not load pending jobs for recovery")
        return

    if not jobs:
        return

    logger.info("worker: recovering %d pending job(s) from DB", len(jobs))
    for job in jobs:
        message_id = job["message_id"]
        chat_id = job["chat_id"]
        full_text = job["full_text"]

        # Reset any "processing" rows back to pending (server crashed mid-flight).
        update_job_status(message_id, STATUS_PENDING)

        # Register in L1 cache to prevent double-enqueue if WAHA retransmits.
        if message_id and message_id not in _processed_ids:
            _processed_ids[message_id] = time.time()

        _queue.put_nowait(_Msg(message_id=message_id, chat_id=chat_id, full_text=full_text))
        logger.info("worker: recovered job message_id=%s chat_id=%s", message_id, chat_id)


async def _worker_loop() -> None:
    logger.info("worker: started")
    while True:
        try:
            msg = await _queue.get()
        except asyncio.CancelledError:
            logger.info("worker: stopped (remaining_queue=%d)", _queue.qsize())
            return

        try:
            await _process_one(msg)
        except asyncio.CancelledError:
            logger.warning(
                "worker: shutdown interrupted in-flight message_id=%s chat=%s — message not retried",
                msg.message_id, msg.chat_id,
            )
            _queue.task_done()
            return
        except Exception:
            logger.exception("worker: unhandled error message_id=%s", msg.message_id)

        _queue.task_done()


def start_worker() -> "asyncio.Task":
    global _worker_task
    _worker_task = asyncio.create_task(_worker_loop(), name="whatsapp-message-worker")
    return _worker_task


async def stop_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    _worker_task = None

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from app.context import request_id_var

logger = logging.getLogger("pa.worker")

# In-process queue — single worker, no persistence across restarts.
_queue: asyncio.Queue = asyncio.Queue()

# Dedup: message_id → epoch timestamp when first seen.
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

    Returns False if message_id was already seen within the dedup window (1 hour).
    Logs a warning when queue depth reaches 10.
    """
    _prune_dedup_cache()
    if message_id and message_id in _processed_ids:
        logger.info(
            "worker: duplicate message_id=%s — skipping",
            message_id,
            extra={"event": "dedup_hit", "message_id": message_id},
        )
        return False
    if message_id:
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

    # Propagate request_id to all awaited code via ContextVar.
    token = request_id_var.set(msg.request_id)
    last_exc: Exception | None = None
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

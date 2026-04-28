import logging
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.config import DATABASE_URL

logger = logging.getLogger("pa.checkpointer")

_checkpointer: AsyncPostgresSaver | None = None
_pool: AsyncConnectionPool | None = None


async def setup_checkpointer() -> None:
    """Create the connection pool, run table migrations, store singleton."""
    global _checkpointer, _pool
    _pool = AsyncConnectionPool(
        conninfo=DATABASE_URL,
        max_size=10,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await _pool.open()
    _checkpointer = AsyncPostgresSaver(_pool)
    await _checkpointer.setup()
    logger.info("Postgres checkpointer tables ready")


async def delete_thread_checkpoints(thread_id: str) -> None:
    """Delete all LangGraph checkpoint data for a thread (irreversible)."""
    if not _pool:
        return
    async with _pool.connection() as conn:
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            try:
                await conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (thread_id,))
            except Exception:
                logger.debug("Table %s not found or delete failed for thread_id=%s", table, thread_id)
    logger.info("Deleted checkpoints for thread_id=%s", thread_id)


def get_checkpointer() -> AsyncPostgresSaver:
    if _checkpointer is None:
        raise RuntimeError("Checkpointer not initialised — call setup_checkpointer() first")
    return _checkpointer

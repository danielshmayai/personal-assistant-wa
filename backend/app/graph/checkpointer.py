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


def get_checkpointer() -> AsyncPostgresSaver:
    if _checkpointer is None:
        raise RuntimeError("Checkpointer not initialised — call setup_checkpointer() first")
    return _checkpointer

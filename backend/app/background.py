"""Tracked background tasks.

`asyncio.ensure_future(coro)` with no reference can be garbage-collected
mid-flight, and its exception is only reported (if ever) when the task object
is destroyed. Memory extraction, episodes and logging all ran that way, so a
failure simply vanished. `spawn` keeps a reference until the task finishes
and logs failures at WARNING with the task name.
"""
import asyncio
import logging

logger = logging.getLogger("pa.background")

_TASKS: set[asyncio.Task] = set()


def spawn(coro, name: str) -> asyncio.Task:
    """Run `coro` in the background (current ContextVars are copied)."""
    task = asyncio.ensure_future(coro)
    try:
        task.set_name(name)
    except AttributeError:
        pass
    _TASKS.add(task)
    task.add_done_callback(_done)
    return task


def _done(task: asyncio.Task) -> None:
    _TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("background task %s failed: %r", task.get_name(), exc, exc_info=exc)


def pending() -> int:
    return len(_TASKS)


async def drain(timeout: float = 5.0) -> None:
    """Wait for in-flight background work (used by tests and shutdown)."""
    if _TASKS:
        await asyncio.wait(list(_TASKS), timeout=timeout)

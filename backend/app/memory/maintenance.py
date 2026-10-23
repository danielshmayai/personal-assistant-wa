"""Memory maintenance — keeps the index fresh and note Summaries current.

Runs inside the app (no external Windows Task Scheduler job, so it also works
in the cloud), separately per data scope:

* index pass (at startup and nightly): reset vectors after an embedding-model
  change, index notes/rules changed since the last run, backfill episodes
  stored without a vector.
* nightly pass (once a day, after 03:00 local time): the index pass, then
  rewrite the Summary of every note with new Log entries, then the
  conversation self-review.
"""
import asyncio
import logging
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage

from app.config import USER_TIMEZONE
from app.memory import obsidian

logger = logging.getLogger("pa.memory.maintenance")

_NIGHTLY_HOUR = 3
_CHECK_EVERY_S = 30 * 60
_LAST_RUN_KEY = "memory_maintenance_last"
_MAX_CONSOLIDATE_PER_RUN = 60

_CONSOLIDATE_PROMPT = """\
You keep a personal knowledge note up to date. Rewrite its SUMMARY so it states the
CURRENT truth about "{title}", using the existing summary and all log entries.

Rules:
- 3-10 short bullet lines ("- ..."), most important first, in the language of the entries.
- Newer entries override older ones when they conflict (e.g. a changed job or status).
- Keep concrete details: names, relations, numbers, dates (YYYY-MM-DD), next steps.
- Drop small talk and anything clearly obsolete. Never invent anything.
- Output ONLY the bullet lines.

EXISTING SUMMARY:
{summary}

LOG ENTRIES (oldest first):
{entries}"""

_task: asyncio.Task | None = None


async def consolidate_note(path) -> bool:
    """Rewrite one note's Summary from its Log. Returns True if rewritten."""
    from app.llm import get_smart_llm, response_text
    from app.runtime import in_thread
    p = await in_thread(obsidian.note_parts, path)
    if not p["entries"]:
        return False
    entries = "\n".join(f"- ({ts}) {c}" for ts, c in p["entries"][-40:])
    prompt = _CONSOLIDATE_PROMPT.format(
        title=p["title"], summary=p["summary"] or "(none yet)", entries=entries[:12000]
    )
    resp = await get_smart_llm().ainvoke([HumanMessage(content=prompt)])
    lines = [ln for ln in response_text(resp).strip().splitlines() if ln.strip().startswith("-")]
    if not lines:
        return False
    await in_thread(obsidian.write_summary, path, "\n".join(lines))
    return True


async def consolidate_vault(limit: int = _MAX_CONSOLIDATE_PER_RUN) -> int:
    """Consolidate notes whose Log grew since their Summary was written.

    A single-entry note with no Summary is left alone: its entry already is
    the summary, and injection shows the newest entries until there's one.
    """
    from app.runtime import in_thread
    done = 0
    for path in await in_thread(obsidian.indexable_notes):
        if done >= limit:
            break
        try:
            parts = await in_thread(obsidian.note_parts, path)
            new = await in_thread(obsidian.unsummarized_entries, path)
            if new == 0 or (not parts["summary"] and len(parts["entries"]) < 2):
                continue
            if await consolidate_note(path):
                done += 1
        except Exception:
            logger.warning("consolidation failed for %s", path, exc_info=True)
    if done:
        logger.info("memory: consolidated %d note summary(ies)", done)
    return done


async def index_pass() -> dict:
    """Bring this scope's vector index up to date. Safe to run often."""
    from app.memory import embeddings
    from app.runtime import in_thread
    reset = await in_thread(embeddings.ensure_embedding_model_current)
    notes = await embeddings.rebuild_vault_embeddings()
    rules = await embeddings.rebuild_rule_embeddings() if reset else 0
    episodes = await embeddings.backfill_episode_embeddings()
    return {"reset": reset, "notes": notes, "rules": rules, "episodes": episodes}


async def nightly_pass() -> dict:
    """Full daily maintenance for the current scope."""
    from app.memory.self_review import analyse_conversations
    result = await index_pass()
    result["consolidated"] = await consolidate_vault()
    result["review"] = await analyse_conversations(24)
    return result


def _local_now() -> datetime:
    return datetime.now(ZoneInfo(USER_TIMEZONE))


async def _run_for_scope(scope: str, nightly: bool) -> None:
    """Run a pass with current_tenant_id set to `scope` (tenant-isolated data)."""
    from app.context import current_tenant_id
    from app.memory.store import get_app_setting, set_app_setting
    from app.runtime import in_thread
    token = current_tenant_id.set(scope)
    try:
        if nightly:
            today = _local_now().strftime("%Y-%m-%d")
            if await in_thread(get_app_setting, _LAST_RUN_KEY) == today:
                return
            result = await nightly_pass()
            await in_thread(set_app_setting, _LAST_RUN_KEY, today)
        else:
            result = await index_pass()
        logger.info("memory maintenance (%s) scope=%r: %s",
                    "nightly" if nightly else "index", scope, result)
    except Exception:
        logger.warning("memory maintenance failed for scope=%r", scope, exc_info=True)
    finally:
        current_tenant_id.reset(token)


async def _loop(scopes: Callable[[], list[str]]) -> None:
    await asyncio.sleep(20)  # let startup finish first
    for scope in scopes():
        await _run_for_scope(scope, nightly=False)
    while True:
        if _local_now().hour >= _NIGHTLY_HOUR:
            for scope in scopes():
                await _run_for_scope(scope, nightly=True)
        await asyncio.sleep(_CHECK_EVERY_S)


def start(scopes: Callable[[], list[str]]) -> None:
    """Start the maintenance loop for the data scopes this process owns.

    The WhatsApp engine passes [""] (owner). The product passes its tenants'
    engine scopes, so the two processes never maintain the same data.
    """
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(scopes), name="memory-maintenance")


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None

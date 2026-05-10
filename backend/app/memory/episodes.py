"""Episodic memory: per-conversation summaries for long-term recall."""

import asyncio
import logging
from langchain_core.messages import HumanMessage
from app.llm import get_smart_llm
from app.memory.store import save_episode, search_episodes

logger = logging.getLogger("pa.episodes")

_SUMMARY_PROMPT = """\
Summarize this conversation in exactly 2-3 sentences.
Cover: (1) the main topic or request, (2) what was resolved or produced, (3) any key facts the user mentioned.

{transcript}"""


async def create_episode(chat_id: str, transcript: str) -> None:
    """Background task: LLM-summarize conversation and store as a searchable episode."""
    if not transcript.strip():
        return
    try:
        from app.memory.embeddings import embed_text, vec_str
        llm = get_smart_llm()
        response = await llm.ainvoke([HumanMessage(content=_SUMMARY_PROMPT.format(transcript=transcript))])
        summary = (response.content or "").strip()
        if not summary:
            return

        loop = asyncio.get_running_loop()
        v = await loop.run_in_executor(None, embed_text, summary)
        vs = vec_str(v) if v else None
        await loop.run_in_executor(None, save_episode, chat_id, summary, vs)
        logger.debug("Episode saved for chat_id=%s", chat_id)
    except Exception:
        logger.debug("create_episode failed for %s", chat_id, exc_info=True)


async def get_relevant_episodes(query: str, limit: int = 3) -> list[str]:
    """Return summaries of past conversations most semantically similar to `query`."""
    try:
        from app.memory.embeddings import embed_text, vec_str
        loop = asyncio.get_running_loop()
        v = await loop.run_in_executor(None, embed_text, query[:2000])
        if not v:
            return []
        vs = vec_str(v)
        return await loop.run_in_executor(None, search_episodes, vs, limit)
    except Exception:
        logger.debug("get_relevant_episodes failed", exc_info=True)
        return []

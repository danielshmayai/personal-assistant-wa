"""
Streaming helpers for the web chat UI.

Exports:
  stream_graph  — re-exported from graph.py
  get_history   — load past messages from the LangGraph checkpointer
"""

import logging
from langchain_core.messages import HumanMessage, AIMessage

logger = logging.getLogger("pa.streaming")

# Re-export so web_chat.py can import from one place
from app.graph.graph import stream_graph  # noqa: F401


async def get_history(chat_id: str, max_messages: int = 60) -> list[dict]:
    """Return the last `max_messages` as {"role": ..., "content": ...} dicts."""
    from app.graph.checkpointer import get_checkpointer
    try:
        cp = get_checkpointer()
        tup = await cp.aget({"configurable": {"thread_id": chat_id}})
        if not tup:
            return []
        messages = tup.checkpoint.get("channel_values", {}).get("messages", [])
        result = []
        for msg in messages[-max_messages:]:
            if isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": str(msg.content or "")})
            elif isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                result.append({"role": "assistant", "content": str(msg.content or "")})
        return result
    except Exception:
        logger.exception("get_history failed for chat_id=%s", chat_id)
        return []

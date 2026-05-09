import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from app.graph.state import PAState
from app.graph.distiller import agent_node, _to_whatsapp
from app.graph.tool_node import tool_executor_node, should_continue
from app.memory.store import load_memory_context, log_conversation
from app.memory.reflection import reflection_node, format_conversation_transcript
from app.memory.episodes import create_episode
from app.graph.checkpointer import get_checkpointer
from app.config import LANGSMITH_API_KEY, LANGSMITH_PROJECT

logger = logging.getLogger("pa.graph")

_RECURSION_LIMIT = 25


def build_graph():
    """Assemble the PA graph.

    START → inject_memory → agent → [tools ↺ | reflection] → END
    """
    builder = StateGraph(PAState)

    builder.add_node("inject_memory", inject_memory_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_executor_node)
    builder.add_node("reflection", reflection_node)

    builder.add_edge(START, "inject_memory")
    builder.add_edge("inject_memory", "agent")
    builder.add_conditional_edges(
        "agent", should_continue, {"tools": "tools", "reflection": "reflection"}
    )
    builder.add_edge("tools", "agent")
    builder.add_edge("reflection", END)

    return builder.compile(checkpointer=get_checkpointer(), debug=False)


async def inject_memory_node(state: PAState) -> dict:
    context = await load_memory_context(state.get("user_input", ""))
    return {"memory_context": context}


# Compiled graph singleton
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _langsmith_meta(request_id: str, chat_id: str) -> dict:
    """Extra RunnableConfig fields for LangSmith — empty when tracing is off."""
    if not LANGSMITH_API_KEY:
        return {}
    return {
        "run_name": f"pa/{chat_id[:16]}",
        "metadata": {"request_id": request_id, "chat_id": chat_id},
        "tags": ["pa"],
    }


def extract_text(content) -> str:
    """Normalise Gemini 2.5+ list-typed content to a plain string."""
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return content or ""


def _last_ai_reply(messages: list) -> str:
    """Return the content of the last AIMessage that has no tool calls."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            return extract_text(msg.content)
    return ""


async def _log(chat_id: str, transcript: str) -> None:
    """Fire-and-forget: write conversation transcript to the log table."""
    try:
        await asyncio.get_running_loop().run_in_executor(None, log_conversation, chat_id, transcript)
    except Exception:
        logger.debug("Failed to log conversation for %s", chat_id)


async def stream_graph(user_text: str, chat_id: str):
    """Async generator of WebSocket event dicts for the web UI."""
    from app.context import request_id_var
    request_id = request_id_var.get() or str(uuid.uuid4())

    graph = _get_graph()
    config = {
        "configurable": {"thread_id": chat_id},
        "recursion_limit": _RECURSION_LIMIT,
        **_langsmith_meta(request_id, chat_id),
    }
    input_state = {
        "user_input": user_text,
        "chat_id": chat_id,
        "messages": [HumanMessage(content=user_text, additional_kwargs={"ts": datetime.now(timezone.utc).isoformat()})],
    }
    reply_parts: list[str] = []

    logger.info(
        "graph: stream start chat_id=%s request_id=%s",
        chat_id,
        request_id,
        extra={"event": "graph_stream_start", "chat_id": chat_id, "request_id": request_id},
    )
    t0 = time.monotonic()

    async for event in graph.astream_events(input_state, config, version="v2"):
        ename = event["event"]
        node = event.get("metadata", {}).get("langgraph_node", "")

        if ename == "on_chat_model_stream" and node == "agent":
            chunk = event["data"].get("chunk")
            if chunk and chunk.content and not getattr(chunk, "tool_call_chunks", None):
                text = extract_text(chunk.content)
                if text:
                    reply_parts.append(text)
                    yield {"type": "token", "content": text}

        elif ename == "on_tool_start":
            yield {
                "type": "tool_start",
                "name": event.get("name", ""),
                "input": event["data"].get("input", {}),
            }

        elif ename == "on_tool_end":
            yield {"type": "tool_end", "name": event.get("name", "")}

    full_reply = "".join(reply_parts)
    duration_ms = round((time.monotonic() - t0) * 1000)
    logger.info(
        "graph: stream end chat_id=%s request_id=%s duration_ms=%d",
        chat_id,
        request_id,
        duration_ms,
        extra={
            "event": "graph_stream_end",
            "chat_id": chat_id,
            "request_id": request_id,
            "duration_ms": duration_ms,
            "reply_len": len(full_reply),
        },
    )

    if full_reply:
        transcript = f"User: {user_text}\nAssistant: {full_reply}"
        asyncio.ensure_future(_log(chat_id, transcript))
        asyncio.ensure_future(create_episode(chat_id, transcript))
    yield {"type": "done", "full_reply": full_reply}


async def run_graph(text: str, chat_id: str) -> str:
    from app.context import request_id_var
    request_id = request_id_var.get() or str(uuid.uuid4())

    graph = _get_graph()
    config = {
        "configurable": {"thread_id": chat_id},
        "recursion_limit": _RECURSION_LIMIT,
        **_langsmith_meta(request_id, chat_id),
    }

    logger.info(
        "graph: run start chat_id=%s request_id=%s",
        chat_id,
        request_id,
        extra={"event": "graph_run_start", "chat_id": chat_id, "request_id": request_id},
    )
    t0 = time.monotonic()

    result = await graph.ainvoke(
        {"user_input": text, "chat_id": chat_id, "messages": [HumanMessage(content=text, additional_kwargs={"ts": datetime.now(timezone.utc).isoformat()})]},
        config=config,
    )

    duration_ms = round((time.monotonic() - t0) * 1000)
    raw = _last_ai_reply(result.get("messages", []))
    reply = _to_whatsapp(raw) or "[No response generated]"

    logger.info(
        "graph: run end chat_id=%s request_id=%s duration_ms=%d",
        chat_id,
        request_id,
        duration_ms,
        extra={
            "event": "graph_run_end",
            "chat_id": chat_id,
            "request_id": request_id,
            "duration_ms": duration_ms,
            "reply_len": len(reply),
        },
    )

    transcript = format_conversation_transcript(result.get("messages", []))
    if transcript:
        asyncio.ensure_future(_log(chat_id, transcript))
        asyncio.ensure_future(create_episode(chat_id, transcript))
    return reply + " ⚡"

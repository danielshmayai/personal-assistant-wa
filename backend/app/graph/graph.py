import logging
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from app.graph.state import PAState
from app.graph.distiller import agent_node, _to_whatsapp
from app.graph.tool_node import tool_executor_node, should_continue
from app.memory.store import load_memory_context
from app.memory.reflection import reflection_node
from app.graph.checkpointer import get_checkpointer

logger = logging.getLogger("pa.graph")


def build_graph():
    """Assemble the PA ReAct graph."""
    builder = StateGraph(PAState)

    builder.add_node("inject_memory", inject_memory_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_executor_node)
    builder.add_node("reflection", reflection_node)

    builder.add_edge(START, "inject_memory")
    builder.add_edge("inject_memory", "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "reflection": "reflection"})
    builder.add_edge("tools", "agent")
    builder.add_edge("reflection", END)

    return builder.compile(checkpointer=get_checkpointer(), debug=False)


async def inject_memory_node(state: PAState) -> dict:
    context = await load_memory_context()
    return {"memory_context": context}


# Compiled graph singleton
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _last_ai_reply(messages: list) -> str:
    """Return the content of the last AIMessage that has no tool calls."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            return msg.content or ""
    return ""


async def stream_graph(user_text: str, chat_id: str):
    """Async generator of WebSocket event dicts for the web UI.

    Yields token/tool_start/tool_end/done dicts, filtering out reflection-node
    tokens and tool-call argument fragments.
    """
    graph = _get_graph()
    config = {"configurable": {"thread_id": chat_id}, "recursion_limit": 8}
    input_state = {
        "user_input": user_text,
        "chat_id": chat_id,
        "messages": [HumanMessage(content=user_text)],
    }
    reply_parts: list[str] = []

    async for event in graph.astream_events(input_state, config, version="v2"):
        ename = event["event"]
        node = event.get("metadata", {}).get("langgraph_node", "")

        if ename == "on_chat_model_stream" and node == "agent":
            chunk = event["data"].get("chunk")
            if chunk and chunk.content and not getattr(chunk, "tool_call_chunks", None):
                reply_parts.append(chunk.content)
                yield {"type": "token", "content": chunk.content}

        elif ename == "on_tool_start":
            yield {
                "type": "tool_start",
                "name": event.get("name", ""),
                "input": event["data"].get("input", {}),
            }

        elif ename == "on_tool_end":
            yield {"type": "tool_end", "name": event.get("name", "")}

    yield {"type": "done", "full_reply": "".join(reply_parts)}


async def run_graph(text: str, chat_id: str) -> str:
    graph = _get_graph()
    config = {
        "configurable": {"thread_id": chat_id},
        "recursion_limit": 8,  # max 3 tool call rounds before giving up
    }
    result = await graph.ainvoke(
        {"user_input": text, "chat_id": chat_id, "messages": [HumanMessage(content=text)]},
        config=config,
    )
    raw = _last_ai_reply(result.get("messages", []))
    reply = _to_whatsapp(raw) or "[No response generated]"
    return reply + " ⚡"

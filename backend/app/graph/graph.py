import logging
from langgraph.graph import StateGraph, START, END
from app.graph.state import PAState
from app.graph.distiller import distiller_node, formatter_node
from app.graph.tool_node import tool_node
from app.memory.store import load_memory_context
from app.memory.reflection import reflection_node

logger = logging.getLogger("pa.graph")


def build_graph() -> StateGraph:
    """Assemble the PA state machine."""
    builder = StateGraph(PAState)

    # Nodes
    builder.add_node("inject_memory", inject_memory_node)
    builder.add_node("distiller", distiller_node)
    builder.add_node("tool_node", tool_node)
    builder.add_node("formatter", formatter_node)
    builder.add_node("reflection", reflection_node)

    # Edges
    builder.add_edge(START, "inject_memory")
    builder.add_edge("inject_memory", "distiller")
    builder.add_edge("distiller", "tool_node")
    builder.add_edge("tool_node", "formatter")
    builder.add_edge("formatter", "reflection")
    builder.add_edge("reflection", END)

    return builder.compile()


async def inject_memory_node(state: PAState) -> dict:
    """Load persistent memory and inject into state before processing."""
    context = await load_memory_context()
    return {"memory_context": context}


# Compiled graph singleton
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def run_graph(text: str, chat_id: str) -> str:
    """Execute the full graph pipeline. Returns the reply string."""
    graph = _get_graph()
    result = await graph.ainvoke({
        "user_input": text,
        "chat_id": chat_id,
        "messages": [],
    })
    return result.get("reply", "[No response generated]")

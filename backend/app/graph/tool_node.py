import logging
from langchain_core.messages import ToolMessage
from app.graph.state import PAState

logger = logging.getLogger("pa.tool_executor")


async def tool_executor_node(state: PAState) -> dict:
    """Execute all tool calls in the last AIMessage and return ToolMessages."""
    from app.google.tools import get_google_tools
    from app.tuya.tools import get_tuya_tools

    chat_id = state.get("chat_id", "")
    tools = get_google_tools(chat_id) + get_tuya_tools()
    tool_map = {t.name: t for t in tools}

    last_msg = state["messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", []) or []

    results = []
    for call in tool_calls:
        name = call["name"]
        args = call.get("args", {})
        tool = tool_map.get(name)
        if not tool:
            content = f"Unknown tool: {name}"
        else:
            try:
                content = str(await tool.ainvoke(args))
                logger.info("Tool %s executed for chat_id=%s", name, chat_id)
            except Exception as e:
                logger.exception("Tool %s failed", name)
                content = f"Tool {name} failed: {e}"

        results.append(ToolMessage(content=content, tool_call_id=call["id"]))

    return {"messages": results}


def should_continue(state: PAState) -> str:
    """Route to tool_executor if the agent made tool calls, else to reflection."""
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    return "reflection"

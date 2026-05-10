import asyncio
import logging
from langchain_core.messages import ToolMessage
from app.graph.state import PAState

logger = logging.getLogger("pa.tool_executor")


async def _run_tool_call(call: dict, tool_map: dict, chat_id: str) -> ToolMessage:
    """Execute a single tool call, returning a ToolMessage even on error."""
    name = call["name"]
    args = call.get("args", {})
    tool = tool_map.get(name)
    if not tool:
        return ToolMessage(content=f"Unknown tool: {name}", tool_call_id=call["id"])
    try:
        content = str(await tool.ainvoke(args))
        logger.info("Tool %s executed for chat_id=%s", name, chat_id)
    except Exception as e:
        logger.exception("Tool %s failed", name)
        content = f"Tool {name} failed: {e}"
    return ToolMessage(content=content, tool_call_id=call["id"])


async def tool_executor_node(state: PAState) -> dict:
    """Execute all tool calls in the last AIMessage concurrently and return ToolMessages."""
    from app.google.tools import get_google_tools
    from app.tuya.tools import get_tuya_tools
    from app.memory.manager import MEMORY_TOOLS
    from app.web.tools import WEB_TOOLS
    from app.tts_tool import TTS_TOOLS
    from app.schedule_tool import get_schedule_tools

    chat_id = state.get("chat_id", "")
    tools = (
        WEB_TOOLS
        + get_google_tools(chat_id)
        + get_tuya_tools()
        + MEMORY_TOOLS
        + TTS_TOOLS
        + get_schedule_tools(chat_id)
    )
    tool_map = {t.name: t for t in tools}

    last_msg = state["messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", []) or []

    results = await asyncio.gather(
        *[_run_tool_call(call, tool_map, chat_id) for call in tool_calls]
    )
    return {"messages": list(results)}


def should_continue(state: PAState) -> str:
    """Route to tool_executor if the agent made tool calls, else to reflection."""
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    return "reflection"

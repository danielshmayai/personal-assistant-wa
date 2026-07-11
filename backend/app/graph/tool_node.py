import asyncio
import logging
from langchain_core.messages import ToolMessage
from app.graph.state import PAState
from app.config import TOOL_CONCURRENCY

logger = logging.getLogger("pa.tool_executor")

_sem = asyncio.Semaphore(TOOL_CONCURRENCY)

# Map tool name prefixes → activity event_type shown in the Activity feed
_TOOL_EVENT_TYPE: dict[str, str] = {
    # Calendar
    "calendar":         "calendar",
    # Email / Gmail
    "gmail":            "email",
    "google_connect":   "email",
    "search_emails":    "email",
    "get_email":        "email",
    "draft_email":      "email",
    "send_email":       "email",
    "list_emails":      "email",
    # Google Drive
    "drive_":             "drive",
    "drive_show":         "drive",
    # Google Maps
    "create_google_map":  "map",
    "add_places_to_map":  "map",
    # Memory / vault
    "save_fact":        "memory",
    "update_rule":      "memory",
    "retrieve_context": "memory",
    "hide_fact":        "memory",
    "hide_rule":        "memory",
    "search_vault":     "memory",
    "list_memory":      "memory",
    "append_to_note":   "memory",
    "read_note":        "memory",
    "grep_note":        "memory",
    # Smart home / Tuya
    "schedule_tuya":    "device",
    "send_tuya":        "device",
    "get_tuya":         "device",
    "list_tuya":        "device",
    "get_device":       "device",
    "control_device":   "device",
    # Scheduled jobs
    "schedule_remind":  "job",
    "list_scheduled":   "job",
    "cancel_scheduled": "job",
    # Nutrition
    "log_meal":         "nutrition",
    "log_water":        "nutrition",
    "nutrition_today":  "nutrition",
    # Web / search
    "web_search":       "search",
    "wikipedia":        "search",
    "fetch_url":        "search",
    "get_weather":      "search",
    "browse_web":       "search",
}

def _event_type_for(tool_name: str) -> str:
    for prefix, etype in _TOOL_EVENT_TYPE.items():
        if tool_name.startswith(prefix):
            return etype
    return "tool"


def _short_args(args: dict) -> str:
    """Return a one-line human-readable summary of the tool arguments."""
    skip = {"token", "api_key", "secret"}
    parts = [f"{k}={str(v)[:60]}" for k, v in args.items() if k not in skip]
    return ", ".join(parts[:3])


async def _run_tool_call(call: dict, tool_map: dict, chat_id: str) -> ToolMessage:
    """Execute a single tool call, returning a ToolMessage even on error."""
    name = call["name"]
    args = call.get("args", {})
    tool = tool_map.get(name)
    if not tool:
        return ToolMessage(content=f"Unknown tool: {name}", tool_call_id=call["id"])
    try:
        async with _sem:
            content = str(await tool.ainvoke(args))
        logger.info("Tool %s executed for chat_id=%s", name, chat_id)
        _log(chat_id, name, args, content, error=None)
    except Exception as e:
        logger.exception("Tool %s failed", name)
        content = f"Tool {name} failed: {e}"
        _log(chat_id, name, args, "", error=str(e))
    return ToolMessage(content=content, tool_call_id=call["id"])


def _log(chat_id: str, tool_name: str, args: dict, result: str, error: str | None) -> None:
    """Fire-and-forget activity log entry (runs in a thread to avoid blocking)."""
    if not chat_id:
        return
    from app.scheduled_jobs import log_activity
    event_type = _event_type_for(tool_name)
    if error:
        summary = f"❌ {tool_name} failed: {error[:120]}"
    else:
        arg_str = _short_args(args)
        summary = f"{tool_name}({arg_str})" if arg_str else tool_name
    detail = {"tool": tool_name, "args": {k: v for k, v in args.items() if k not in ("token","api_key","secret")}}
    if error:
        detail["error"] = error
    else:
        detail["result_preview"] = result[:300]
    try:
        import threading
        threading.Thread(target=log_activity, args=(chat_id, event_type, summary, detail), daemon=True).start()
    except Exception:
        pass


async def tool_executor_node(state: PAState) -> dict:
    """Execute all tool calls in the last AIMessage concurrently and return ToolMessages."""
    from app.graph.tools_registry import get_all_tools

    chat_id = state.get("chat_id", "")
    tools = get_all_tools(chat_id, state.get("tenant_id", ""), state.get("enabled_modules"))
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

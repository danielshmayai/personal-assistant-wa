import logging
import re
from app.graph.state import PAState

logger = logging.getLogger("pa.tool_node")

# Keyword patterns for each tool — checked against lowercased user input
TOOL_PATTERNS = [
    ("google_connect", [
        r"connect.*(gmail|google|calendar|email)",
        r"(gmail|google|calendar|email).*(connect|link|authorize|setup|integrate)",
        r"link.*(gmail|google|calendar|email)",
        r"authorize.*(gmail|google|calendar)",
        r"setup.*(gmail|google|calendar)",
    ]),
    ("gmail_read", [
        r"(read|check|show|get|fetch|list).*(email|mail|inbox|messages?)",
        r"(email|mail|inbox).*(read|check|show|get|fetch|list)",
        r"(any|new|recent|latest|unread).*(email|mail|messages?)",
        r"what.*(email|mail)",
    ]),
    ("gmail_send", [
        r"send.*(email|mail)",
        r"(email|mail).*(send|write|compose|draft)",
        r"write.*email",
        r"compose.*email",
    ]),
    ("calendar_list", [
        r"(show|list|get|check|what).*(event|meeting|appointment|schedule|calendar)",
        r"(calendar|schedule|agenda).*(show|list|get|check|what|today|tomorrow|week)",
        r"(today|tomorrow|this week).*(meeting|event|appointment|schedule)",
        r"what.*(today|tomorrow|schedule|plan)",
    ]),
    ("calendar_create", [
        r"(create|add|schedule|set up|book|plan).*(event|meeting|appointment|reminder|call)",
        r"(event|meeting|appointment|reminder).*(create|add|schedule|set up|book)",
        r"remind me",
        r"set a reminder",
    ]),
]


def _detect_tool(text: str) -> str | None:
    """Return the first matching tool name for the given text, or None."""
    lowered = text.lower()
    for tool_name, patterns in TOOL_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, lowered):
                return tool_name
    return None


async def tool_node(state: PAState) -> dict:
    """Keyword-route user input to a Google tool; execute it and store results."""
    chat_id = state.get("chat_id", "")
    user_input = state.get("user_input", "")

    tool_name = _detect_tool(user_input)
    if not tool_name:
        return {"tool_results": ""}

    logger.info("Keyword-matched tool=%s for input: %.80s", tool_name, user_input)

    try:
        from app.google.tools import get_google_tools
        tools = get_google_tools(chat_id)
    except Exception:
        logger.exception("Failed to load Google tools")
        return {"tool_results": ""}

    tool_map = {t.name: t for t in tools}
    tool = tool_map.get(tool_name)
    if not tool:
        return {"tool_results": ""}

    try:
        result = tool.invoke({})
        logger.info("Tool %s executed for chat_id=%s", tool_name, chat_id)
        return {"tool_results": f"[{tool_name}]\n{result}"}
    except Exception:
        logger.exception("Tool %s failed", tool_name)
        return {"tool_results": f"[{tool_name}]\nTool execution failed."}

import json
import logging
import re
from langchain_core.messages import HumanMessage, SystemMessage
from app.graph.state import PAState

logger = logging.getLogger("pa.tool_node")

# Tools that require structured args extracted from user input
_ARGS_REQUIRED = {"gmail_send", "calendar_create"}

_ARG_EXTRACT_PROMPT = """\
Extract the required arguments for the tool `{tool_name}` from the user message.
Return ONLY a valid JSON object with these exact keys:

{schema}

Rules:
- Use null for any field that is not mentioned.
- For datetimes use ISO 8601 format (e.g. "2026-04-15T14:00:00").
- Do not add any explanation — JSON only.

User message: {text}"""

_TOOL_SCHEMAS = {
    "gmail_send": '{"to": "<email address>", "subject": "<subject line>", "body": "<email body>"}',
    "calendar_create": '{"title": "<event title>", "start_datetime": "<ISO 8601>", "end_datetime": "<ISO 8601>", "attendees": "<comma-separated emails or empty string>"}',
}


async def _extract_args(tool_name: str, user_input: str) -> dict:
    """Use Gemini to extract tool arguments from the user message."""
    from app.llm import get_gemini_llm, get_llm
    from app.config import GEMINI_API_KEY
    llm = get_gemini_llm() if GEMINI_API_KEY else get_llm()
    prompt = _ARG_EXTRACT_PROMPT.format(
        tool_name=tool_name,
        schema=_TOOL_SCHEMAS[tool_name],
        text=user_input,
    )
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    raw = response.content.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)

# Keyword patterns for each tool — checked against lowercased user input
TOOL_PATTERNS = [
    ("google_connect", [
        # English
        r"connect.*(gmail|google|calendar|email)",
        r"(gmail|google|calendar|email).*(connect|link|authorize|setup|integrate)",
        r"link.*(gmail|google|calendar|email)",
        r"authorize.*(gmail|google|calendar)",
        r"setup.*(gmail|google|calendar)",
        # Hebrew
        r"התחבר.*(gmail|google|יומן|אימייל|מייל)",
        r"(gmail|google|יומן|אימייל|מייל).*(התחבר|חבר|קשר|הרשה)",
    ]),
    ("gmail_read", [
        # English
        r"(read|check|show|get|fetch|list).*(email|mail|inbox|messages?)",
        r"(email|mail|inbox).*(read|check|show|get|fetch|list)",
        r"(any|new|recent|latest|unread).*(email|mail|messages?)",
        r"what.*(email|mail)",
        # Hebrew — keyword anywhere in sentence (word order varies)
        r"(בדוק|הצג|קרא|תביא|תראה|איזה|אילו|כמה).*(אימייל|מייל|דואר|inbox)",
        r"(אימייל|מייל|דואר|inbox).*(בדוק|הצג|קרא|חדש|אחרון|יש לי|קיבלתי|מאתמול|היום|השבוע)",
        r"(אימיילים|מיילים).*(יש לי|קיבלתי|חדשים|אחרונים|מאתמול|היום|השבוע)",
        r"(יש לי|קיבלתי).*(אימייל|מייל|דואר)",
        r"מה.*יש.*ב.*(אימייל|מייל|דואר)",
        r"(אימייל|מייל|אימיילים|מיילים).*\?",
    ]),
    ("gmail_send", [
        # English
        r"send.*(email|mail)",
        r"(email|mail).*(send|write|compose|draft)",
        r"write.*email",
        r"compose.*email",
        # Hebrew
        r"שלח.*(אימייל|מייל|דואר)",
        r"(אימייל|מייל).*(שלח|כתוב|צור)",
        r"כתוב.*מייל",
    ]),
    ("calendar_create", [
        # English — must come before calendar_list to avoid false list matches
        r"(create|add|schedule|set up|book|plan).*(event|meeting|appointment|reminder|call)",
        r"(event|meeting|appointment|reminder).*(create|add|schedule|set up|book)",
        r"remind me",
        r"set a reminder",
        # Hebrew — action verbs (הוסף/צור/קבע/תזמן) are strong create signals
        r"(צור|הוסף|קבע|תזמן).*(פגישה|אירוע|תזכורת|שיחה|מפגש)",
        r"(פגישה|אירוע|תזכורת).*(צור|הוסף|קבע|תזמן)",
        r"תזכיר לי",
        r"קבע פגישה",
        r"הוסף פגישה",
        r"צור אירוע",
    ]),
    ("calendar_list", [
        # English
        r"(show|list|get|check|what).*(event|meeting|appointment|schedule|calendar)",
        r"(calendar|schedule|agenda).*(show|list|get|check|what|today|tomorrow|week)",
        r"(today|tomorrow|this week).*(meeting|event|appointment|schedule)",
        r"what.*(today|tomorrow|schedule|plan)",
        # Hebrew — keyword anywhere in sentence
        r"(בדוק|הצג|תראה|איזה|אילו|מה).*(יומן|פגישה|אירוע|לוח)",
        r"(יומן|פגישה|אירוע|לוח זמנים).*(בדוק|הצג|מה|יש לי|היום|מחר|השבוע)",
        r"(יש לי|קורה).*(היום|מחר|השבוע)",
        r"(היום|מחר|השבוע).*(יומן|פגישה|אירוע|תוכנית|מה)",
        r"מה יש לי",
        r"מה קורה",
        r"(פגישות|אירועים).*(יש לי|היום|מחר|השבוע)",
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
        if tool_name in _ARGS_REQUIRED:
            try:
                args = await _extract_args(tool_name, user_input)
                # Replace null values with sensible defaults
                args = {k: (v if v is not None else "") for k, v in args.items()}
                logger.info("Extracted args for %s: %s", tool_name, args)
            except Exception:
                logger.exception("Arg extraction failed for %s", tool_name)
                return {"tool_results": f"[{tool_name}]\nCould not extract required fields from your message."}
        else:
            args = {}

        result = tool.invoke(args)
        logger.info("Tool %s executed for chat_id=%s", tool_name, chat_id)
        return {"tool_results": f"[{tool_name}]\n{result}"}
    except Exception:
        logger.exception("Tool %s failed", tool_name)
        return {"tool_results": f"[{tool_name}]\nTool execution failed."}

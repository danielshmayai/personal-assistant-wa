import logging
import re
from datetime import datetime
from langchain_core.messages import SystemMessage, AIMessage
from app.llm import get_gemini_llm
from app.graph.state import PAState

logger = logging.getLogger("pa.agent")

# Concise system prompt — short = fewer tokens on every request
_SYSTEM_TEMPLATE = """\
You are danidin, a personal assistant on WhatsApp.
Current date and time: {datetime}.

You have tools for Gmail, Google Calendar, and Tuya smart-home devices.
Use them whenever the user asks about emails, meetings, calendar, or home devices (lights, switches, etc.).
If Google is not connected, call google_connect and share the link.

WhatsApp formatting rules (always follow):
- *bold* (single asterisk), _italic_ (single underscore)
- Never use ** or # headers
- Lists: use • or - per line
- Keep responses short and phone-friendly"""


def _build_system_prompt(memory_context: str) -> str:
    now = datetime.now().strftime("%A, %d %B %Y, %H:%M")
    prompt = _SYSTEM_TEMPLATE.format(datetime=now)
    if memory_context:
        prompt += f"\n\nAbout the user:\n{memory_context}"
    return prompt


def _to_whatsapp(text: str) -> str:
    """Convert common markdown to WhatsApp-compatible format."""
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    text = re.sub(r'__(.+?)__', r'*\1*', text)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


async def agent_node(state: PAState) -> dict:
    """Single Gemini node: decides which tool to call (if any) and generates the reply."""
    from app.google.tools import get_google_tools
    from app.tuya.tools import get_tuya_tools

    chat_id = state.get("chat_id", "")
    tools = get_google_tools(chat_id) + get_tuya_tools()

    llm = get_gemini_llm().bind_tools(tools)
    system = _build_system_prompt(state.get("memory_context", ""))

    # Send last 20 messages for context — full history lives in checkpointer
    messages = [SystemMessage(content=system)] + list(state["messages"])[-20:]

    response = await llm.ainvoke(messages)
    logger.info("Agent response for chat_id=%s tool_calls=%s", chat_id, bool(getattr(response, "tool_calls", None)))
    return {"messages": [response]}

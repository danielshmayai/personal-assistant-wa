import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from langchain_core.messages import SystemMessage, AIMessage
from app.llm import get_gemini_llm
from app.graph.state import PAState
from app.config import USER_TIMEZONE

logger = logging.getLogger("pa.agent")

# Concise system prompt — short = fewer tokens on every request
_SYSTEM_TEMPLATE = """\
You are danidin, a personal assistant on WhatsApp.
Current date and time: {datetime} — this is accurate, trust it. Never ask the user for the current time.

You have tools for Gmail, Google Calendar, Tuya smart-home devices, and long-term memory.
Use them whenever the user asks about emails, meetings, calendar, or home devices (lights, switches, etc.).
If Google is not connected, call google_connect and share the link.

*Memory tools — use proactively:*
- save_fact: call whenever the user shares personal info worth remembering across sessions (name, job, family, location, preferences, important dates, recurring events, etc.)
- save_rule: call whenever the user gives a behavioral instruction: "always", "never", "from now on", "prefer", "stop doing X", "don't do Y". Also call when the user explicitly says "remember to always…" or "as a rule…"
- list_memory: call when the user asks "what do you know/remember about me?", "show my rules", "what have you saved?"
- delete_fact: call when the user says "forget X", "remove that fact about X", "delete my X"
- delete_rule: call when the user says "remove rule N", "forget that rule about X", "delete that preference" (use list_memory first to find the ID)

When the user says *"remember that…"* or *"note that…"* — always save it immediately as a fact or rule (whichever fits best) and confirm.

WhatsApp formatting rules (always follow):
- *bold* (single asterisk), _italic_ (single underscore)
- Never use ** or # headers
- Lists: use • or - per line
- Keep responses short and phone-friendly"""


def _build_system_prompt(memory_context: str) -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    now = datetime.now(tz=tz).strftime(f"%A, %d %B %Y, %H:%M ({USER_TIMEZONE})")
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
    from app.memory.manager import MEMORY_TOOLS

    chat_id = state.get("chat_id", "")
    tools = get_google_tools(chat_id) + get_tuya_tools() + MEMORY_TOOLS

    llm = get_gemini_llm().bind_tools(tools)
    system = _build_system_prompt(state.get("memory_context", ""))

    # Send last 20 messages for context — full history lives in checkpointer
    messages = [SystemMessage(content=system)] + list(state["messages"])[-20:]

    response = await llm.ainvoke(messages)
    logger.info("Agent response for chat_id=%s tool_calls=%s", chat_id, bool(getattr(response, "tool_calls", None)))
    return {"messages": [response]}

import logging
import re
from datetime import datetime
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from app.llm import get_llm, get_gemini_llm
from app.config import GEMINI_API_KEY
from app.graph.state import PAState, DistilledIntent

logger = logging.getLogger("pa.distiller")

DISTILLER_SYSTEM_PROMPT = """\
You are an intent classifier for a Personal Assistant.
Analyze the user's message and classify it into exactly ONE category:

- Development_Task: anything related to coding, bugs, features, deployments, PRs, tech tasks.
- Financial_Log: expenses, income, payments, invoices, budgets, financial tracking.
- General_Reminder: reminders, notes, todos, calendar items, or anything that doesn't fit above.

Also determine if a Development_Task is specifically a bug/defect report.

Respond ONLY with valid JSON matching this schema:
{
  "category": "Development_Task" | "Financial_Log" | "General_Reminder",
  "is_bug": true/false,
  "summary": "concise summary of the request"
}
"""

BUG_TEMPLATE = """\
ACTUAL BEHAVIOR:
{actual}

EXPECTED BEHAVIOR:
{expected}

HOW-TO-REPRODUCE:
{repro}

Env URL:
{env_url}

Octane / OPB / Sync builds:
{builds}"""

BUG_FORMAT_PROMPT = """\
The user reported a bug. Extract the details and format them strictly as:

ACTUAL BEHAVIOR:
<what actually happened>

EXPECTED BEHAVIOR:
<what should have happened>

HOW-TO-REPRODUCE:
<numbered steps>

Env URL:
<URL or "N/A">

Octane / OPB / Sync builds:
<build info or "N/A">

If any field is not mentioned, write "Not specified" for that field.
Do NOT add any other text — return ONLY the filled template.

User's bug report:
{text}"""


async def distiller_node(state: PAState) -> dict:
    """Classify the user's raw input into a structured intent."""
    text = state["user_input"]
    llm = get_llm()

    messages = [
        SystemMessage(content=DISTILLER_SYSTEM_PROMPT),
        HumanMessage(content=text),
    ]
    try:
        structured_llm = llm.with_structured_output(DistilledIntent)
        intent: DistilledIntent = await structured_llm.ainvoke(messages)
        intent.raw_text = text
    except Exception:
        logger.exception("Structured output failed, falling back to General_Reminder")
        intent = DistilledIntent(
            category="General_Reminder",
            is_bug=False,
            summary=text[:200],
            raw_text=text,
        )

    logger.info("Distilled: category=%s is_bug=%s", intent.category, intent.is_bug)
    return {"intent": intent}


def _to_whatsapp(text: str) -> str:
    """Convert common markdown to WhatsApp-compatible format."""
    # **bold** → *bold*
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    # __bold__ → *bold*
    text = re.sub(r'__(.+?)__', r'*\1*', text)
    # ## Heading → *Heading*
    text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
    # Collapse 3+ consecutive blank lines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


async def formatter_node(state: PAState) -> dict:
    """Format the reply using Gemini. Ollama is used only for classification (distiller_node)."""
    intent = state["intent"]
    chat_id = state.get("chat_id", "")
    if not intent:
        return {"reply": "I couldn't understand that. Could you rephrase?"}

    # Bug/defect → strict Octane template via Gemini
    if intent.category == "Development_Task" and intent.is_bug:
        llm = get_gemini_llm() if GEMINI_API_KEY else get_llm()
        prompt = BUG_FORMAT_PROMPT.format(text=intent.raw_text)
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        return {"reply": response.content or "[No response generated]"}

    now = datetime.now()
    current_dt = now.strftime("%A, %d %B %Y, %H:%M")

    system_parts = [
        f"You are danidin, a helpful personal assistant.\n"
        f"Current date and time: {current_dt}.\n\n"
        "You can read emails, send emails, list calendar events, and create calendar events via Google tools. "
        "If tool results are provided below, use them to answer naturally. "
        "If no tool results are present but the user asked about email or calendar, say the action will be performed.\n\n"
        "FORMATTING RULES — always follow these:\n"
        "- Use WhatsApp markdown only: *bold* (single asterisk), _italic_ (single underscore).\n"
        "- Never use ** double asterisks or # headers.\n"
        "- Separate items with real line breaks, not semicolons.\n"
        "- For lists use • or - at the start of each line.\n"
        "- Keep responses concise and easy to read on a phone screen."
    ]

    if state.get("memory_context"):
        system_parts.append(f"Here are things you know about the user:\n{state['memory_context']}")

    all_messages = state.get("messages") or []
    history_turns = []
    for msg in all_messages[-12:]:
        if isinstance(msg, HumanMessage):
            history_turns.append(f"User: {msg.content}")
        elif isinstance(msg, AIMessage):
            history_turns.append(f"Assistant: {msg.content}")
    if history_turns:
        system_parts.append("Recent conversation:\n" + "\n".join(history_turns))

    if state.get("tool_results"):
        system_parts.append(
            f"Tool results:\n{state['tool_results']}\n"
            "Use the above data to answer. Do not mention 'tool results' — just answer naturally."
        )

    invoke_messages = [
        SystemMessage(content="\n\n".join(system_parts)),
        HumanMessage(content=intent.raw_text),
    ]

    llm = get_gemini_llm() if GEMINI_API_KEY else get_llm()
    response = await llm.ainvoke(invoke_messages)
    reply = _to_whatsapp(response.content or "") or "[No response generated]"

    if GEMINI_API_KEY:
        reply = reply + " ⚡"
        logger.info("Formatter used Gemini for chat_id=%s", chat_id)

    new_entries = [HumanMessage(content=intent.raw_text), AIMessage(content=reply)]
    total = len(all_messages) + len(new_entries)
    if total > 20:
        excess = total - 20
        new_entries = new_entries[excess:] if excess < len(new_entries) else []

    return {"reply": reply, "messages": new_entries}

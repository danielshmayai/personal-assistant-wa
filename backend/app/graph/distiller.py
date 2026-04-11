import logging
from langchain_core.messages import SystemMessage, HumanMessage
from app.llm import get_llm
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


async def formatter_node(state: PAState) -> dict:
    """Format the reply based on the distilled intent."""
    intent = state["intent"]
    if not intent:
        return {"reply": "I couldn't understand that. Could you rephrase?"}

    # Bug/defect → strict Octane template
    if intent.category == "Development_Task" and intent.is_bug:
        llm = get_llm()
        prompt = BUG_FORMAT_PROMPT.format(text=intent.raw_text)
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        return {"reply": response.content}

    # All other intents → general LLM response with memory context
    llm = get_llm()
    system_parts = ["You are a helpful personal assistant."]
    if state.get("memory_context"):
        system_parts.append(
            f"Here are things you know about the user:\n{state['memory_context']}"
        )

    messages = [
        SystemMessage(content="\n\n".join(system_parts)),
        HumanMessage(content=intent.raw_text),
    ]
    response = await llm.ainvoke(messages)
    return {"reply": response.content}

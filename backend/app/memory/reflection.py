"""Reflection node — detects corrections and extracts lessons to save as rules."""

import logging
import re
from langchain_core.messages import HumanMessage
from app.llm import get_llm
from app.graph.state import PAState
from app.memory.store import insert_rule

logger = logging.getLogger("pa.reflection")

CORRECTION_DETECT_PROMPT = """\
Analyze this conversation exchange. The user sent a message and received a reply.
Determine if the user is CORRECTING the assistant (e.g., "no, I meant...", "that's wrong", "don't do X", "actually...").

If a correction is detected, extract:
1. The lesson/rule to remember for the future.
2. A brief reason why.

Respond in exactly this format if a correction is found:
CORRECTION_DETECTED
RULE: <the rule to save>
REASON: <why>

If NO correction is detected, respond with exactly:
NO_CORRECTION

User message: {user_input}
Assistant reply: {reply}"""


async def reflection_node(state: PAState) -> dict:
    """Check if the user's message contains a correction. If so, save the lesson."""
    from langchain_core.messages import AIMessage as _AIMessage
    user_input = state.get("user_input", "")
    # Reply is the last AIMessage without tool calls
    reply = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, _AIMessage) and not getattr(msg, "tool_calls", None):
            reply = msg.content or ""
            break

    if not user_input or not reply:
        return {}

    # Only check messages that look like corrections (cheap heuristic first).
    correction_signals = [
        "no,", "no ", "not that", "wrong", "don't", "dont", "stop",
        "actually", "i meant", "correction", "that's incorrect",
    ]
    input_lower = user_input.lower()
    if not any(signal in input_lower for signal in correction_signals):
        return {}

    llm = get_llm()
    prompt = CORRECTION_DETECT_PROMPT.format(user_input=user_input, reply=reply)

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = (response.content or "").strip()

        if content.startswith("CORRECTION_DETECTED"):
            rule_line = ""
            reason_line = ""
            for line in content.split("\n"):
                rule_match = re.search(r'(?i)RULE:\s*(.+)', line)
                reason_match = re.search(r'(?i)REASON:\s*(.+)', line)
                if rule_match:
                    rule_line = rule_match.group(1).strip()
                elif reason_match:
                    reason_line = reason_match.group(1).strip()

            if rule_line:
                insert_rule(rule_line, reason_line, source="reflection")
                logger.info("Reflection saved rule: %s", rule_line)
    except Exception:
        logger.exception("Reflection node failed (non-fatal)")

    return {}

"""Reflection node — detects corrections and preference updates, saves them as permanent rules."""

import logging
import re
from langchain_core.messages import HumanMessage
from app.llm import get_smart_llm
from app.graph.state import PAState
from app.memory import obsidian

logger = logging.getLogger("pa.reflection")

# Signals that suggest the user is correcting, refining, or teaching the bot.
# Intentionally broad — false positives are cheap; false negatives lose learning.
_CORRECTION_SIGNALS = [
    # Direct negations
    "no,", "no ", "nope", "nah", "not that", "not what i",
    # Correction phrases
    "wrong", "incorrect", "mistake", "error", "that's not", "that was not",
    "that isn't", "not right", "you got it wrong", "you're wrong",
    # Redirection
    "don't", "dont", "stop", "never", "avoid", "please don't", "please stop",
    "i don't want", "i dont want",
    # Clarification
    "actually", "i meant", "i mean", "what i meant", "i was asking",
    "i said", "i asked", "correction", "to clarify", "let me clarify",
    # Preference updates
    "prefer", "i prefer", "i'd rather", "i would rather", "i like",
    "i hate", "from now on", "always", "in the future", "next time",
    "going forward", "for future", "remember to", "make sure to",
]

_REFLECT_PROMPT = """\
You analyze a conversation exchange to extract any lessons the assistant should remember forever.

Look for two types of learning opportunities:
1. *Correction* — the user is pointing out that the previous reply was wrong, misunderstood, or unwanted.
2. *Preference/Rule* — the user is expressing how they want the assistant to behave in the future \
(e.g. "always reply in Hebrew", "never add emojis", "from now on format dates as DD/MM").

For each lesson found, decide whether it is:
- A *rule* (behavioral instruction, formatting preference, do/don't directive) → output as RULE
- A *fact* (personal information, preference about themselves, not about bot behavior) → output as FACT

Respond with one block per lesson in exactly this format:
LESSON
TYPE: RULE or FACT
KEY_OR_RULE: <short key if FACT, or the rule sentence if RULE>
VALUE_OR_REASON: <value if FACT, or reason/context if RULE>
END_LESSON

If there is nothing to learn, respond with exactly:
NOTHING_TO_LEARN

---
User message: {user_input}
Assistant reply: {reply}"""


async def reflection_node(state: PAState) -> dict:
    """Detect corrections and preference updates in the latest exchange; persist lessons."""
    from langchain_core.messages import AIMessage as _AIMessage

    user_input = state.get("user_input", "")
    reply = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, _AIMessage) and not getattr(msg, "tool_calls", None):
            reply = msg.content or ""
            break

    if not user_input or not reply:
        return {}

    # Cheap heuristic: skip if the message has none of the correction/preference signals
    input_lower = user_input.lower()
    if not any(signal in input_lower for signal in _CORRECTION_SIGNALS):
        return {}

    llm = get_smart_llm()
    prompt = _REFLECT_PROMPT.format(user_input=user_input, reply=reply)

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = (response.content or "").strip()

        if "NOTHING_TO_LEARN" in content:
            return {}

        # Parse all LESSON blocks
        for block in re.findall(r"LESSON\s*(.*?)\s*END_LESSON", content, re.DOTALL):
            type_match = re.search(r"TYPE:\s*(RULE|FACT)", block, re.IGNORECASE)
            key_match = re.search(r"KEY_OR_RULE:\s*(.+)", block)
            val_match = re.search(r"VALUE_OR_REASON:\s*(.+)", block)

            if not (type_match and key_match):
                continue

            lesson_type = type_match.group(1).upper()
            key_or_rule = key_match.group(1).strip()
            value_or_reason = val_match.group(1).strip() if val_match else ""

            if lesson_type == "RULE":
                obsidian.update_rule(key_or_rule)
                logger.info("Reflection saved rule: %s", key_or_rule)
            elif lesson_type == "FACT":
                # Reflection prompt doesn't extract category — default to Preferences.
                # The fact body is value_or_reason; entity slug uses key_or_rule.
                obsidian.save_fact("Preferences", key_or_rule, value_or_reason or key_or_rule)
                logger.info("Reflection saved fact: %s = %s", key_or_rule, value_or_reason)

    except Exception:
        logger.exception("Reflection node failed (non-fatal)")

    return {}

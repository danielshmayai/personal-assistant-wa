"""Reflection node — fires after every reply; extracts rules, facts, and behavioral signals."""

import asyncio
import logging
import re
from langchain_core.messages import HumanMessage, AIMessage
from app.llm import get_smart_llm
from app.graph.state import PAState
from app.memory import obsidian

logger = logging.getLogger("pa.reflection")

_REFLECT_PROMPT = """\
You analyze a conversation to extract lessons the assistant should permanently remember.

Look for five types of learning opportunities:

1. **Correction** — the user points out the previous reply was wrong, misunderstood, or unwanted.
2. **Rule/Preference** — the user expresses how they want the assistant to behave in the future
   (e.g. "always reply in Hebrew", "never add emojis", "from now on format dates as DD/MM").
3. **Positive reinforcement** — the user confirms, thanks, or expresses satisfaction with a specific
   style, format, depth, or approach. Extract *what worked* so the assistant can repeat it.
4. **Proactive fact** — the user mentions personal information in passing (name, location, relationship,
   ongoing project, preference, date) even without explicitly asking to "remember" it.
5. **Implicit dissatisfaction** — the user rephrases the same question, asks for more detail
   ("elaborate", "tell me more", "expand"), or restates what they actually meant. This signals the
   previous answer was off-target, too shallow, or misunderstood. Extract a rule about how to handle
   similar questions better next time (e.g. "When asked about X, provide more detail on Y").

{implicit_note}For each lesson found, classify it as:
- **RULE** — a behavioral instruction, formatting preference, or do/don't directive
- **FACT** — personal information or a preference *about the user themselves*

Respond with one block per lesson in exactly this format:
LESSON
TYPE: RULE or FACT
KEY_OR_RULE: <short key if FACT, or the full rule sentence if RULE>
VALUE_OR_REASON: <value if FACT, or reason/context if RULE>
END_LESSON

If there is nothing to learn, respond with exactly:
NOTHING_TO_LEARN

---
Conversation (most recent last):
{conversation}"""

# Drill-down phrases that signal the previous answer was too shallow
_DRILL_DOWN = [
    "tell me more", "more detail", "more details", "elaborate", "expand on",
    "can you explain", "explain more", "go deeper", "dig deeper", "what do you mean",
    "be more specific", "give me an example", "give examples",
]

# Short ack phrases that signal clear satisfaction
_POSITIVE_ACKS = [
    "perfect", "exactly", "great", "excellent", "that's it", "that's right",
    "well done", "nice", "good job", "correct", "spot on", "👍",
]


def _detect_implicit_signals(messages: list) -> str:
    """Return a contextual note for the prompt if implicit feedback patterns are found."""
    from langchain_core.messages import HumanMessage as _HM
    human_msgs = [
        (m.content if isinstance(m.content, str) else "")
        for m in messages
        if isinstance(m, _HM)
    ]
    if len(human_msgs) < 2:
        return ""

    last = human_msgs[-1].lower()

    if any(p in last for p in _DRILL_DOWN):
        return "NOTE: The user asked for more detail — the previous answer was likely too brief.\n\n"

    if any(p in last for p in _POSITIVE_ACKS):
        return "NOTE: The user gave a strong positive signal — extract what specifically worked.\n\n"

    # Rephrase detection: last two user messages share many words (>50% overlap)
    if len(human_msgs) >= 2:
        prev_words = set(human_msgs[-2].lower().split())
        curr_words = set(last.split())
        if prev_words and curr_words:
            overlap = len(prev_words & curr_words) / max(len(prev_words), len(curr_words))
            if overlap > 0.5 and len(curr_words) > 3:
                return "NOTE: The user rephrased their previous question — the earlier answer likely missed their intent.\n\n"

    return ""


def format_conversation_transcript(messages: list, max_turns: int = 6) -> str:
    """Serialize the last max_turns human+AI exchanges as a readable transcript."""
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, list):
                content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
            lines.append(f"User: {content.strip()}")
        elif isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            content = msg.content
            if isinstance(content, list):
                content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
            if content.strip():
                lines.append(f"Assistant: {content.strip()}")
    # Keep last max_turns * 2 lines (user + assistant per turn)
    return "\n".join(lines[-(max_turns * 2):])


async def _run_reflection(state: PAState) -> None:
    """Analyze the conversation and persist any lessons. Runs as a background task."""
    messages = state.get("messages", [])
    conversation = format_conversation_transcript(messages)
    if not conversation:
        return

    implicit_note = _detect_implicit_signals(messages)
    llm = get_smart_llm()
    prompt = _REFLECT_PROMPT.format(conversation=conversation, implicit_note=implicit_note)

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = (response.content or "").strip()

        if "NOTHING_TO_LEARN" in content:
            return

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
                from app.memory.embeddings import find_similar_rule, store_rule_embedding
                import asyncio as _asyncio
                loop = _asyncio.get_running_loop()
                from app.runtime import in_thread
                similar = await in_thread(find_similar_rule, key_or_rule)
                if similar and similar.strip().lower() == key_or_rule.strip().lower():
                    continue  # semantically identical rule already exists
                if similar:
                    obsidian.hide_rule(similar)  # retire outdated version
                obsidian.update_rule(key_or_rule)
                await in_thread(store_rule_embedding, key_or_rule)
                logger.info("Reflection saved rule: %s", key_or_rule)
            elif lesson_type == "FACT":
                obsidian.save_fact("Preferences", key_or_rule, value_or_reason or key_or_rule)
                logger.info("Reflection saved fact: %s = %s", key_or_rule, value_or_reason)

    except Exception:
        logger.exception("Reflection failed (non-fatal)")


async def reflection_node(state: PAState) -> dict:
    """Fire reflection as a non-blocking background task; return immediately."""
    asyncio.ensure_future(_run_reflection(state))
    return {}

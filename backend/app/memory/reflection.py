"""Memory extraction — runs after every reply and files what was learned.

Replaces the old reflection step, which re-read the last 6 turns on every
message and dumped every "fact" as a one-line Preferences/<key>.md file with
no dedup. Now:

* Only the NEW exchange is extracted from (earlier turns are context only),
  so the same fact isn't re-filed on every following message.
* The model returns structured JSON and each item is routed to where a person
  would look for it: People/, Projects/, Entities/, the user's Profile note,
  Profile/Commitments, or the Rules file.
* What's already known (the memory injected for this turn) is passed in, so
  the model only reports genuinely new information; save_fact also skips
  content a note already contains.
* Every write is recorded in memory_events (brief, feed, health panel).
* Off when the tenant's "memory" module is disabled.
"""

import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage

from app.config import USER_TIMEZONE
from app.graph.state import PAState
from app.llm import get_smart_llm, response_text
from app.memory import obsidian

logger = logging.getLogger("pa.reflection")

_MIN_CONFIDENCE = 0.6
_MAX_ITEMS = 8
_SELF_ENTITIES = {"", "me", "user", "i", "myself", "the user", "אני", "המשתמש"}

_EXTRACT_PROMPT = """\
You maintain the long-term memory of a personal assistant that serves ONE user.
Read the NEW EXCHANGE and extract only information worth remembering for weeks or
months. Return ONLY a JSON array (use [] when there is nothing). Each item:
{{"kind": "...", "entity": "...", "content": "...", "confidence": 0.0-1.0}}

kind:
- "person": someone in the user's life — relation, role, workplace, contact details,
  birthday, preferences, what's going on with them. entity = their name as the user calls them.
- "project": an ongoing effort or goal (work or personal) — status, next step, deadline,
  numbers. entity = the project's name.
- "decision": a decision the user made. entity = the related project or person.
- "preference": how the user likes things (food, tools, style, schedule). entity = "me".
- "fact": a durable fact about the user (job, home, family, health, possessions, plans)
  → entity = "me"; or about an organization/place/thing → entity = its name.
- "commitment": something the user promised or intends to do, with or without a time
  ("I'll call the lawyer Thursday"). entity = who/what it's for.
- "rule": how the ASSISTANT should behave from now on — explicit instructions
  ("always answer in Hebrew") AND corrections of how it behaved ("no, don't summarize,
  give me the full list"). content = one imperative sentence. entity = "".

content: ONE self-contained sentence in the user's language. Resolve relative dates to
YYYY-MM-DD (today is {today}). Include names so it reads correctly out of context.

Do NOT extract: questions, one-off requests, small talk, temporary states ("I'm tired"),
anything only the assistant said, data the assistant already logged with a tool (meals,
water, workouts, body metrics, reminders), or anything already in KNOWN MEMORY.

KNOWN MEMORY (already stored — never repeat):
{known}

EARLIER CONTEXT (already processed — use only to resolve references like "her"/"it"):
{context}

NEW EXCHANGE:
{exchange}"""


def _text(content) -> str:
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return content or ""


def format_conversation_transcript(messages: list, max_turns: int = 6) -> str:
    """Serialize the last max_turns human+AI exchanges as a readable transcript."""
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            lines.append(f"User: {_text(msg.content).strip()}")
        elif isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            content = _text(msg.content).strip()
            if content:
                lines.append(f"Assistant: {content}")
    # Keep last max_turns * 2 lines (user + assistant per turn)
    return "\n".join(lines[-(max_turns * 2):])


def split_new_exchange(messages: list) -> tuple[str, str]:
    """(new_exchange, earlier_context) — the last user turn + final reply, and
    up to two turns before it."""
    last_human = None
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            last_human = i
            break
    if last_human is None:
        return "", ""
    new = format_conversation_transcript(messages[last_human:], max_turns=1)
    earlier = format_conversation_transcript(messages[:last_human], max_turns=2)
    return new, earlier


def _parse_items(raw: str) -> list[dict]:
    """Pull the JSON array out of the model reply (tolerates code fences/prose)."""
    m = re.search(r"\[.*\]", raw or "", re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    items = []
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind", "")).strip().lower()
        content = str(it.get("content", "")).strip()
        try:
            conf = float(it.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0
        if kind and content and conf >= _MIN_CONFIDENCE:
            items.append({"kind": kind, "entity": str(it.get("entity", "") or "").strip(),
                          "content": content})
    return items[:_MAX_ITEMS]


def route(item: dict) -> tuple[str, str] | None:
    """(category, entity) for a vault item; None for rules (handled separately)."""
    kind, entity = item["kind"], item["entity"]
    is_self = entity.lower() in _SELF_ENTITIES
    if kind == "rule":
        return None
    if kind == "person" and not is_self:
        return "People", entity
    if kind in ("project", "decision") and not is_self:
        return "Projects", entity
    if kind == "commitment":
        return obsidian.PROFILE_CATEGORY, "Commitments"
    if kind == "fact" and not is_self:
        return "Entities", entity
    return obsidian.PROFILE_CATEGORY, obsidian.PROFILE_ENTITY


async def _save_rule(text: str) -> str | None:
    from app.memory.embeddings import find_similar_rule, store_rule_embedding
    from app.runtime import in_thread
    similar = await in_thread(find_similar_rule, text)
    if similar and similar.strip().lower() == text.strip().lower():
        return None
    if similar:
        await in_thread(obsidian.hide_rule, similar)  # the refined rule supersedes it
    result = await in_thread(obsidian.update_rule, text)
    if not result.startswith("Saved"):
        return None
    await in_thread(store_rule_embedding, text)
    return text


async def extract_and_store(messages: list, known_memory: str = "") -> list[dict]:
    """Extract durable information from the newest exchange and file it.

    Returns what was stored: [{kind, entity, path, content}].
    """
    from app.memory.store import record_memory_event
    from app.runtime import in_thread

    exchange, context = split_new_exchange(messages)
    user_line = exchange.split("\n", 1)[0].removeprefix("User:").strip()
    if len(user_line) < 4:
        return []

    today = datetime.now(ZoneInfo(USER_TIMEZONE)).strftime("%Y-%m-%d (%A)")
    prompt = _EXTRACT_PROMPT.format(
        today=today,
        known=(known_memory or "(nothing yet)")[:3000],
        context=context or "(none)",
        exchange=exchange,
    )
    response = await get_smart_llm().ainvoke([HumanMessage(content=prompt)])
    items = _parse_items(response_text(response))

    stored: list[dict] = []
    for item in items:
        if item["kind"] == "rule":
            rule = await _save_rule(item["content"])
            if rule:
                await in_thread(record_memory_event, "rule", "", "System/Rules.md", rule)
                stored.append({**item, "path": "System/Rules.md"})
            continue
        target = route(item)
        if not target:
            continue
        category, entity = target
        result = await in_thread(obsidian.save_fact, category, entity, item["content"])
        if not result.startswith("Saved to "):
            continue  # already known, or invalid name
        path = result.removeprefix("Saved to ").strip()
        await in_thread(record_memory_event, item["kind"], entity, path, item["content"])
        stored.append({**item, "path": path})

    if stored:
        logger.info("memory: stored %d item(s): %s", len(stored),
                    ", ".join(f"{s['kind']}→{s['path']}" for s in stored))
    return stored


def _memory_enabled(state: PAState) -> bool:
    modules = state.get("enabled_modules")
    return modules is None or "memory" in modules


async def reflection_node(state: PAState) -> dict:
    """Start extraction in the background (tracked) and return immediately."""
    if not _memory_enabled(state):
        return {}
    from app.background import spawn
    spawn(
        extract_and_store(list(state.get("messages", [])), state.get("memory_context", "")),
        name=f"memory-extract:{state.get('chat_id', '')[:24]}",
    )
    return {}

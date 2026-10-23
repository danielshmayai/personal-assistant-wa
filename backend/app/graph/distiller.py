import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage, ToolMessage
from app.llm import get_chat_llm
from app.graph.state import PAState
from app.config import USER_TIMEZONE

logger = logging.getLogger("pa.agent")

# The prompt is a short core plus one section per tool group, and only the
# sections for groups bound this turn are included: describing Gmail or Tuya
# to an account that has them off cost tokens and caused wrong tool picks.
# What each tool does lives in its docstring; sections carry only the routing
# rules that span tools.
_CORE = """\
You are {assistant_identity}.
{datetime_block}
Dates: the block above comes from the server clock and is always correct — never use your training data for today's date or weekday. Compute "tomorrow", "next week" etc. from TODAY's ISO date.

{tool_summary}

Act, don't announce: when a request needs a tool, call it in this same turn before replying. Never write "I will…" without having called the tool. Pass ids from [MEDIA …] tags (message_id) unchanged."""

_GROUP_LABELS = {
    "web": "web search", "google": "Gmail, Calendar, Drive and Maps", "tuya": "smart-home devices",
    "memory": "long-term memory", "schedule": "reminders and scheduled actions",
    "nutrition": "nutrition logging", "fitness": "fitness tracking", "image": "image analysis",
    "tts": "voice replies",
}

_SECTIONS = {
    "web": """\
*Web:* use web tools proactively for anything current (news, prices, people, weather, a pasted link) — never say you can't browse.""",

    "google": """\
*Google:* if Gmail/Calendar/Drive isn't connected, call google_connect and share the link.
- A [MEDIA type=document …] tag → drive_save_document right away with the best category from the caption or file type, then say where it was saved.
- Maps: "הצג במפה" / "show on map" without listing places → use the place just discussed; don't ask. Always return the map link.""",

    "image": """\
*Images — when a [MEDIA type=image …] tag arrives, pick ONE path by the caption (in this order):*
1. Body-composition sheet (InBody/Tanita/scale — "דף סיכום", "מדדים", "שקילה", "הרכב גוף") → analyze_body_scan.
2. Asks to SAVE ("שמור", "תשמור", names a folder/album) → save_photo_tagged (subfolder/description/tags from the caption; leave empty what it doesn't say).
3. A question about the image ("מה זה?", "איזה דגם?", "מה כתוב פה?") → analyze_image with the caption verbatim. Nothing is saved.
4. A workout or meal to log ("תרשום את האימון", "אכלתי את זה") → analyze_image, then log_workout / log_meal with what it shows.
5. No caption → analyze_image, then offer the matching next step. Never save without being asked.
A saved photo's link is found later through memory (search for its description); drive_show_image displays it.""",

    "tuya": """\
*Smart home:* control_device acts now (most devices use switch_1 for on/off); for a later time use schedule_tuya_command.""",

    "schedule": """\
*Reminders:* "remind me…" / "תזכיר לי…", or "תזכור ש…" WITH any time word (מחר, היום, בשעה, בערב, עוד X, tomorrow, tonight, at, in X minutes) → schedule_reminder (or schedule_recurring_reminder for "every…"), not memory.""",

    "nutrition": """\
*Nutrition tracker{nutrition_goal}:* when the user says they ate or drank something, log it immediately (log_meal / log_water) and confirm with the estimate — don't ask for exact grams. nutrition_today reports protein vs {protein_target}, carbs, calories and micronutrients.""",

    "fitness": """\
*Fitness & training tracker{fitness_note}:* finished a workout → log_workout. Wants a plan → suggest_workout (doesn't log). ANY question about a past session or trend (including Garmin-imported ones) → fitness_history; never say you have no information without checking.""",

    "memory": """\
*Memory is captured automatically:* after every reply, durable information (people, projects, decisions, preferences, commitments, instructions about how you should behave) is filed in the user's vault, and the relevant parts appear under "Long-term memory".
- Use it naturally and never ask for something it already says. Don't call save_fact for things merely mentioned in passing.
- When the user says they will do something at a time ("I'll call the lawyer Thursday"), offer a reminder in ONE short line.
- Explicit "remember that…" / "תזכור ש…" with NO time word → save it now (save_fact, or update_rule for how you should behave) and confirm in a few words, no file paths.
- "What do you know about X" → search_memory; to count or filter a stored list use search_memory with `note` rather than read_note.
- "Forget X" / "that's wrong" → forget.
- Asked what you can do → read_note("System/Capabilities.md") and present it.""",

    "tts": "",
}

# Order sections appear in (image routing references fitness/nutrition tools).
_SECTION_ORDER = ("memory", "schedule", "web", "google", "image", "nutrition", "fitness", "tuya", "tts")


def _compose_base(groups: set[str] | None) -> str:
    """Core + the sections for the enabled tool groups (None = all)."""
    enabled = [g for g in _SECTION_ORDER if groups is None or g in groups]
    if enabled:
        summary = "You have tools for " + ", ".join(_GROUP_LABELS[g] for g in enabled) + "."
    else:
        summary = "You have tools for nothing this turn — just talk."
    parts = [_CORE.replace("{tool_summary}", summary)]
    parts += [_SECTIONS[g] for g in enabled if _SECTIONS[g]]
    return "\n\n".join(parts)


# Owner-specific fragments, templated so tenant prompts stay generic.
# Owner values must reproduce the historical prompt byte-for-byte; tenants
# must never see the owner's name, dietary targets, or medical framing.
_OWNER_FRAGMENTS = {
    "{assistant_identity}": "danidin, a personal assistant",
    "{nutrition_goal}": " — goal is high protein (target 110g/day) and low carbs",
    "{protein_target}": "the 110g target",
    "{fitness_note}": " (personal medical constraints always apply — see profile)",
}
_TENANT_FRAGMENTS = {
    "{assistant_identity}": "the user's personal assistant",
    "{nutrition_goal}": "",
    "{protein_target}": "the user's daily target",
    "{fitness_note}": "",
}

_WA_FORMAT = """

WhatsApp formatting rules (always follow):
- *bold* (single asterisk), _italic_ (single underscore)
- Never use ** or # headers
- Lists: use • or - per line
- Keep responses short and phone-friendly"""

_WEB_FORMAT = """

Formatting: use full Markdown — **bold**, _italic_, # headers, ```code blocks```, tables.
Responses may be longer and well-structured when helpful."""


_OLLAMA_FALLBACK_NOTE = (
    "\n\nIMPORTANT: No Gemini model is currently usable for this account (the "
    "configured API key doesn't work), so you are running on a local fallback "
    "model with NO TOOLS available — no web search, email, calendar, memory, "
    "smart-home, scheduling, nutrition, or fitness logging. Have a plain "
    "conversation only. If asked to do something requiring a tool, say you "
    "can't right now and that the user should add a valid Gemini API key "
    "under Settings → Secrets to restore full functionality. Never claim to "
    "have performed an action you cannot actually take."
)


def _build_system_prompt(
    memory_context: str, chat_id: str = "", ollama_fallback: bool = False,
    groups: set[str] | None = None,
) -> str:
    """groups: the tool groups bound this turn (None = all, the owner path)."""
    tz = ZoneInfo(USER_TIMEZONE)
    now = datetime.now(tz=tz)

    # Build an unambiguous multi-line datetime block that Gemini cannot misread.
    # Include ISO date (no locale ambiguity), numeric day-of-week, and a pre-computed
    # reference calendar so relative scheduling (tomorrow, next week…) is always correct.
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_num = now.weekday()  # 0=Monday … 6=Sunday
    tomorrow = now + timedelta(days=1)
    day_after = now + timedelta(days=2)
    # Build this week Mon–Sun
    week_start = now - timedelta(days=day_num)
    week_lines = "  " + "\n  ".join(
        f"{day_names[i]}: {(week_start + timedelta(days=i)).strftime('%Y-%m-%d')}"
        for i in range(7)
    )
    datetime_block = (
        f"TODAY (server clock, always correct):\n"
        f"  Day:      {day_names[day_num]} (weekday #{day_num + 1}, where 1=Monday)\n"
        f"  ISO date: {now.strftime('%Y-%m-%d')}\n"
        f"  Time:     {now.strftime('%H:%M')} ({USER_TIMEZONE})\n"
        f"TOMORROW:         {tomorrow.strftime('%Y-%m-%d')} ({day_names[tomorrow.weekday()]})\n"
        f"DAY AFTER TOMORROW: {day_after.strftime('%Y-%m-%d')} ({day_names[day_after.weekday()]})\n"
        f"THIS WEEK:\n{week_lines}"
    )

    addendum = _WEB_FORMAT if chat_id.startswith("web") else _WA_FORMAT
    base = _compose_base(set() if ollama_fallback else groups)
    prompt = (base + addendum).replace("{datetime_block}", datetime_block)
    from app.context import current_tenant_id, current_user_name, current_user_email
    fragments = _TENANT_FRAGMENTS if current_tenant_id.get() else _OWNER_FRAGMENTS
    for placeholder, value in fragments.items():
        prompt = prompt.replace(placeholder, value)
    # Who am I talking to? Injected from the authenticated session (product web
    # app). Without this the agent is identity-blind and mistakes the logged-in
    # user for a third-party contact (e.g. treats "connect Daniel's account" as
    # someone else's). Empty on the legacy owner/WhatsApp path → block omitted.
    user_name = current_user_name.get()
    user_email = current_user_email.get()
    if user_name or user_email:
        who = " ".join(p for p in (user_name, f"({user_email})" if user_email else "") if p)
        prompt += (
            f"\n\nWHO YOU ARE TALKING TO: {who}. This is the logged-in owner of "
            "this account. When they say \"my account\", \"my email\", \"connect my "
            "Google/Gmail/Calendar/Drive\", \"my calendar\", etc., they mean THIS "
            "person and THIS email — never treat their own name or email as a "
            "third-party contact. Only act on someone else's account if they "
            "explicitly name a different person."
        )
    if memory_context:
        # Near the top, not after ~85 lines of tool rules: what the assistant
        # knows about the user should shape every reply, not trail the prompt.
        memory_block = (
            "## Long-term memory (what you already know — use it naturally, "
            "don't recite it, and don't ask for things listed here)\n"
            f"{memory_context}\n\n"
        )
        marker = "You have tools for"
        idx = prompt.find(marker)
        prompt = (prompt[:idx] + memory_block + prompt[idx:]) if idx >= 0 else prompt + "\n\n" + memory_block
    if ollama_fallback:
        prompt += _OLLAMA_FALLBACK_NOTE
    return prompt


def _to_whatsapp(text: str) -> str:
    """Convert common markdown to WhatsApp-compatible format."""
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    text = re.sub(r'__(.+?)__', r'*\1*', text)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _sanitize_for_gemini(messages: list, n: int = 20) -> list:
    """Slice last n messages and ensure valid Gemini turn ordering.

    Gemini requires strict alternation and complete function-call sequences.
    This function handles three failure modes:
      1. Window sliced mid-sequence (orphaned ToolMessage or AIMessage(tool_calls) at head)
      2. Dangling AIMessage(tool_calls) at tail with no following ToolMessage
      3. `function_call` key in additional_kwargs conflicting with tool_calls
    """
    window = list(messages)[-n:]
    while window and not isinstance(window[0], HumanMessage):
        window = window[1:]

    # Pass 1 — normalize ALL AIMessages: flatten list content, strip function_call.
    normalized: list = []
    for msg in window:
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, list):
                content = "".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
            # function_call in additional_kwargs conflicts with tool_calls for Gemini
            extra = {
                k: v
                for k, v in (getattr(msg, "additional_kwargs", None) or {}).items()
                if k != "function_call"
            }
            msg = AIMessage(
                content=content,
                tool_calls=list(getattr(msg, "tool_calls", None) or []),
                id=getattr(msg, "id", None),
                additional_kwargs=extra,
                response_metadata=dict(getattr(msg, "response_metadata", None) or {}),
            )
        normalized.append(msg)

    # Pass 2 — enforce complete tool-call sequences; drop dangling ones.
    result: list = []
    i = 0
    while i < len(normalized):
        msg = normalized[i]
        if isinstance(msg, AIMessage) and msg.tool_calls:
            # Collect the ToolMessages that immediately follow
            j = i + 1
            while j < len(normalized) and isinstance(normalized[j], ToolMessage):
                j += 1
            if j > i + 1:
                # Complete sequence — include AI turn + all tool responses
                result.extend(normalized[i:j])
                i = j
            else:
                # Dangling — no tool response in window; emit content-only if available
                if msg.content:
                    result.append(AIMessage(
                        content=msg.content,
                        id=getattr(msg, "id", None),
                        additional_kwargs=dict(msg.additional_kwargs),
                        response_metadata=dict(msg.response_metadata),
                    ))
                i += 1
        elif isinstance(msg, ToolMessage):
            # Orphaned ToolMessage (preceding AIMessage was removed) — skip
            if result and isinstance(result[-1], AIMessage) and getattr(result[-1], "tool_calls", None):
                result.append(msg)
            i += 1
        else:
            result.append(msg)
            i += 1

    # Re-trim in case cleanup exposed a new non-Human head
    while result and not isinstance(result[0], HumanMessage):
        result.pop(0)

    return result


async def agent_node(state: PAState) -> dict:
    """Single Gemini node: decides which tool to call (if any) and generates the reply."""
    import traceback
    from app.graph.tools_registry import enabled_groups, get_all_tools

    chat_id = state.get("chat_id", "")
    tools = get_all_tools(chat_id, state.get("tenant_id", ""), state.get("enabled_modules"))

    tool_names = [t.name for t in tools]
    logger.info("agent_node: binding %d tools: %s", len(tool_names), tool_names)

    try:
        llm, engine, model = get_chat_llm()
        logger.info("agent_node: engine=%s model=%s chat_id=%s", engine, model, chat_id)
        if engine == "ollama":
            # Local fallback model's function-calling support is unreliable —
            # a plain conversational reply beats a broken tool-calling attempt.
            # (Reached only when no Gemini model works for this tenant's key.)
            logger.warning("agent_node: no usable Gemini model for chat_id=%s — falling back to Ollama without tools", chat_id)
        else:
            llm = llm.bind_tools(tools)
    except Exception as e:
        tb = traceback.format_exc()
        logger.error("bind_tools FAILED: %s\n%s", e, tb)
        error_msg = (
            f"⚠️ bind_tools נכשל: `{e}`\n\n"
            f"כלים שנטענו ({len(tool_names)}):\n"
            + "\n".join(f"• {n}" for n in tool_names)
            + f"\n\n```\n{tb[-1500:]}\n```"
        )
        return {"messages": [AIMessage(content=error_msg)]}

    system = _build_system_prompt(
        state.get("memory_context", ""), state.get("chat_id", ""), ollama_fallback=(engine == "ollama"),
        groups=enabled_groups(state.get("enabled_modules")),
    )

    sanitized = _sanitize_for_gemini(state["messages"])
    logger.info("agent_node: sending %d messages to Gemini", len(sanitized))
    messages = [SystemMessage(content=system)] + sanitized

    try:
        response = await asyncio.wait_for(llm.ainvoke(messages), timeout=30.0)
    except asyncio.TimeoutError:
        logger.error("Gemini API call timed out after 30s for chat_id=%s", chat_id)
        return {"messages": [AIMessage(content="⚠️ Gemini timeout (>30s). Please try again.")]}
    except Exception as e:
        tb = traceback.format_exc()
        logger.error("Gemini ainvoke FAILED: %s\n%s", e, tb)
        error_msg = (
            f"⚠️ Gemini קריאה נכשלה: `{type(e).__name__}: {e}`\n\n"
            f"```\n{tb[-2000:]}\n```"
        )
        return {"messages": [AIMessage(content=error_msg)]}

    try:
        response.additional_kwargs["ts"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        pass
    logger.info("Agent response for chat_id=%s tool_calls=%s", chat_id, bool(getattr(response, "tool_calls", None)))
    return {"messages": [response]}

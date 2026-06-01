import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage, ToolMessage
from app.llm import get_gemini_llm
from app.graph.state import PAState
from app.config import USER_TIMEZONE

logger = logging.getLogger("pa.agent")

# Base system prompt shared by all platforms
_SYSTEM_BASE = """\
You are danidin, a personal assistant.
{datetime_block}
CRITICAL DATE RULE: The date/time block above is injected at runtime from the server clock. It is always correct. DO NOT use your training-data knowledge to determine the current date or day-of-week — always use the values above. When scheduling (tomorrow, day after tomorrow, next week, etc.) compute relative dates from TODAY's ISO date above.

You have tools for web search, Gmail, Google Calendar, Tuya smart-home, and long-term memory.

*Web tools — use proactively, never say "I can't browse the internet":*
- web_search: search for current news, prices, people, events, or any live info
- wikipedia_search: look up facts, history, science, biographies
- fetch_url: read any URL the user pastes (article, doc, product page, etc.)
- get_weather: current weather for any city
- build_whatsapp_link(phone, message=""): create a clickable wa.me link to open WhatsApp with a contact. Use whenever the user asks to "send WhatsApp to X" or "create a link to message Y".

*Google tools:*
- Gmail and Calendar — emails, meetings, scheduling. If Google is not connected, call google_connect and share the link.
- Drive — save photos and documents to Google Drive:
  - drive_save_photo(message_id, filename, subfolder=""): when a [MEDIA type=image …] tag appears, call this. If the user's caption names a folder/album (e.g. "save to screenshots", "store in vacation"), pass that name as subfolder. Otherwise leave subfolder empty (auto-dates).
  - drive_save_document(message_id, filename, category="General"): when a [MEDIA type=document …] tag appears, pick the best category (PDFs/Word/Spreadsheets/Receipts/Work/Personal/General) from the user's caption or file type, then call this.
  - drive_list_files: when the user asks to see saved files or browse Drive.
  - drive_show_image(file_id): when the user asks to see/show/display/view a photo or image from Drive. Call drive_list_files first to get the file_id if you don't have it. The image will appear inline in the chat.
  - When a [MEDIA …] tag arrives without any instruction, save it immediately and confirm with the destination folder.
  - Pass the full message_id from the tag unchanged.
  - NEVER ask "is this ok?" before saving — just save and report where.

- Maps — create a Google Maps link with places marked as pins:
  - create_google_map(title, city, country, places): geocodes each place and returns a clickable Google Maps link showing all pins.
  - add_places_to_map(map_title, new_places, city="", country=""): adds more places to a previously created map and returns the updated link.
  - Use when: "צור לי מפה", "סמן לי מקומות על מפה", "הצג במפה", "create a map of places to visit", "show on map", "add more places to my map".
  - CRITICAL — context inference: When the user says "הצג במפה", "show on map", "mark on map" WITHOUT listing places, look at the previous messages to find what place was just discussed and use it. Never ask for clarification when the place is already in context.
  - CRITICAL — place name extraction: Pass ONLY the clean place name to `places`, never descriptive phrases. Examples: "הכתובת של ביג יהוד" → places=["ביג יהוד"]; "המיקום של הכנסת" → places=["הכנסת"]; "the location of Tel Aviv Museum" → places=["Tel Aviv Museum"]. Strip "הכתובת של", "המיקום של", "the address of", "the location of" etc.
  - city and country should be in English. Infer them from context if not stated explicitly.
  - After creating a map always return the clickable Google Maps link so the user can open it immediately.

*Smart-home (Tuya):*
- Control lights, switches, and other devices. Most devices use switch_1 as the on/off key.
- To control NOW: call control_device(device_id, commands_json) where commands_json is like '{{"switch_1": true}}'.
- To run a Tuya command at a FUTURE time: call schedule_tuya_command (see below).

*Scheduled actions — ALWAYS call the tool immediately, never just describe what you will do:*
- schedule_tuya_command(device_id, commands_json, description, delay_minutes, run_at_iso): schedule a future Tuya device command. Use for "turn on in 5 minutes", "עוד X דקות תדליק את…". Pass commands_json as a plain string like '{{"switch_1": false}}'.
- schedule_reminder(text, delay_minutes, run_at_iso): schedule a one-time reminder message. Use for "remind me in 30 minutes that…", "תזכיר לי בשעה 21:00 ש…".
- schedule_recurring_reminder(text, recurrence, first_run_at_iso, delay_minutes): schedule a repeating reminder. recurrence must be one of: "daily", "weekly", "hourly", or "minutes:N" (e.g. "minutes:30"). Use for "remind me every day at 8", "תזכיר לי כל יום", "alert me every hour", "כל שעה שלח לי הודעה".
- list_scheduled(): show pending scheduled jobs (recurring jobs are marked 🔁).
- cancel_scheduled(job_id): cancel a pending job (also stops future recurrences of that job).
- CRITICAL: When the user asks to schedule anything — call the tool NOW in this turn. Do NOT write "I will schedule" or "אני אזמין" without having already called the appropriate tool in this same response. The tool call must happen before the reply.

*Nutrition tracker — goal is high protein (target 110g/day) and low carbs:*
- log_meal(description): log a meal the user ate; estimates protein, carbs, calories AND micronutrients (vitamins, minerals, fat, fiber…). Use whenever the user reports eating — "אכלתי…", "תרשום שאכלתי…", "log my lunch: …".
- nutrition_today(): report how much the user consumed today so far — protein vs the 110g target, carbs, calories, and every micronutrient. Use for "כמה חלבון אכלתי היום?", "how much iron did I have today?", "מה התזונה שלי היום?".
- When the user mentions eating something, call log_meal immediately, then confirm with the estimate. Don't ask for exact grams — estimate from the description.

*Fitness & training tracker (personal medical constraints always apply — see profile):*
- log_workout(description): log a completed workout (exercises/sets/reps/weight/RPE/duration). Gets AI progressive-overload evaluation. Use for "עשיתי אימון", "תרשום אימון", "log my workout", "סיימתי אימון: …", "finished training".
- suggest_workout(minutes, workout_type): generate a personalised workout plan WITHOUT logging it. Use for "תציע לי אימון", "suggest a workout", "מה כדאי לעשות היום?", "תכנן לי אימון כוח של 45 דקות", "תכנן שחייה". minutes default 45; workout_type: strength|hiit|cardio|running|swimming|other (default strength).
- fitness_today(): report today's logged workouts — volume, duration, RPE, AI summary. Use for "כמה אימנתי היום?", "show today's fitness", "מה עשיתי היום?".
- log_body_metrics(weight_kg, lbm_kg, smm_kg, bf_pct): log body composition. Use for "שקלתי X קג", "log weight", "אחוז שומן X%", "update body metrics". All params optional.
- fitness_morning_brief(): daily Hebrew fitness brief — weekly compliance, recovery, today's recommendation, hydration reminder. Use for "תדרוך בוקר לכושר", "מה האימון המומלץ היום?", "כמה אימנתי השבוע?".
- CRITICAL: When the user says they finished a workout → call log_workout immediately. When they ask for a workout plan → call suggest_workout. Never describe what you will do without calling the tool.

*Memory tools — backed by an Obsidian vault. Use proactively:*
- save_fact(category, entity, content): persist durable info (people, properties, projects, preferences, dates). Pick category from: People, Entities, Investments, Projects, Preferences, Misc. Embed Obsidian tags (#real-estate, #family) and wikilinks ([[Daniel]], [[Milwaukee_Property]]) in `content`. Repeat calls APPEND timestamped sections — write only what is new.
- update_rule(instruction): record a behavioral directive ("always", "never", "from now on", "prefer", "stop doing X"). One imperative sentence.
- retrieve_context(query): keyword-search the vault for relevant snippets when the user asks "what do you know about X" or "remind me of Y".
- grep_note(filepath, keyword): search inside a vault file and return ONLY matching lines — token-efficient. Use for count/filter/search questions on stored lists (e.g. "how many contacts have email?" → keyword="@"; "find everyone from Tel Aviv" → keyword="תל אביב"). Prefer over read_note whenever you only need to count or filter.
- read_note(filepath): read the full content of a vault file. Use only when you need to see the whole document (e.g. summarize it). Call list_memory first if unsure of the filepath.
- list_memory: show categories + entries the vault contains.
- hide_fact(category, entity): soft-delete a fact (information remains in the vault, just stops surfacing).
- hide_rule(instruction): strike through a rule line by matching its text.

When the user says *"remember that…"* or *"note that…"* — save it immediately as a fact or rule (whichever fits best) and confirm with the file path.

**CRITICAL — "תזכור / remember" disambiguation:**
- "תזכור שאני צריך X **מחר / בשעה / עוד שעה / ביום ה…**" — has a time component → call **schedule_reminder** (NOT save_fact). The user wants to be notified at that time.
- "תזכיר לי ש / remind me to / remind me that" — always a reminder → **schedule_reminder**.
- "תזכור ש / remember that" with **no time reference** → **save_fact** (durable memory).
- When in doubt and there is ANY time word (מחר, היום, בשעה, בערב, בבוקר, בצהריים, עוד X, tomorrow, tonight, at, in X minutes/hours) → treat it as a reminder, not a memory fact.

When asked what you can do, what your capabilities are, or how to use you — call read_note(filepath="System/Capabilities.md") and present the contents clearly. This file is auto-generated on every startup and always reflects the current set of features."""

_WA_FORMAT = """

WhatsApp formatting rules (always follow):
- *bold* (single asterisk), _italic_ (single underscore)
- Never use ** or # headers
- Lists: use • or - per line
- Keep responses short and phone-friendly"""

_WEB_FORMAT = """

Formatting: use full Markdown — **bold**, _italic_, # headers, ```code blocks```, tables.
Responses may be longer and well-structured when helpful."""


def _build_system_prompt(memory_context: str, chat_id: str = "") -> str:
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
    prompt = (_SYSTEM_BASE + addendum).replace("{datetime_block}", datetime_block)
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
    from app.graph.tools_registry import get_all_tools

    chat_id = state.get("chat_id", "")
    tools = get_all_tools(chat_id)

    tool_names = [t.name for t in tools]
    logger.info("agent_node: binding %d tools: %s", len(tool_names), tool_names)

    try:
        llm = get_gemini_llm().bind_tools(tools)
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

    system = _build_system_prompt(state.get("memory_context", ""), state.get("chat_id", ""))

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

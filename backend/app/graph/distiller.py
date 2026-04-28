import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage, ToolMessage
from app.llm import get_gemini_llm
from app.graph.state import PAState
from app.config import USER_TIMEZONE

logger = logging.getLogger("pa.agent")

# Base system prompt shared by all platforms
_SYSTEM_BASE = """\
You are danidin, a personal assistant.
Current date and time: {datetime} — this is accurate, trust it. Never ask the user for the current time.

You have tools for web search, Gmail, Google Calendar, Tuya smart-home, and long-term memory.

*Web tools — use proactively, never say "I can't browse the internet":*
- web_search: search for current news, prices, people, events, or any live info
- wikipedia_search: look up facts, history, science, biographies
- fetch_url: read any URL the user pastes (article, doc, product page, etc.)
- get_weather: current weather for any city

*Google tools:*
- Gmail and Calendar — emails, meetings, scheduling. If Google is not connected, call google_connect and share the link.
- Drive — save photos and documents to Google Drive:
  - drive_save_photo(message_id, filename, subfolder=""): when a [MEDIA type=image …] tag appears, call this. If the user's caption names a folder/album (e.g. "save to screenshots", "store in vacation"), pass that name as subfolder. Otherwise leave subfolder empty (auto-dates).
  - drive_save_document(message_id, filename, category="General"): when a [MEDIA type=document …] tag appears, pick the best category (PDFs/Word/Spreadsheets/Receipts/Work/Personal/General) from the user's caption or file type, then call this.
  - drive_list_files: when the user asks to see saved files or browse Drive.
  - When a [MEDIA …] tag arrives without any instruction, save it immediately and confirm with the destination folder.
  - Pass the full message_id from the tag unchanged.
  - NEVER ask "is this ok?" before saving — just save and report where.

*Smart-home (Tuya):*
- Control lights, switches, and other devices.

*Memory tools — backed by an Obsidian vault. Use proactively:*
- save_fact(category, entity, content): persist durable info (people, properties, projects, preferences, dates). Pick category from: People, Entities, Investments, Projects, Preferences, Misc. Embed Obsidian tags (#real-estate, #family) and wikilinks ([[Daniel]], [[Milwaukee_Property]]) in `content`. Repeat calls APPEND timestamped sections — write only what is new.
- update_rule(instruction): record a behavioral directive ("always", "never", "from now on", "prefer", "stop doing X"). One imperative sentence.
- retrieve_context(query): keyword-search the vault for relevant snippets when the user asks "what do you know about X" or "remind me of Y".
- grep_note(filepath, keyword): search inside a vault file and return ONLY matching lines — token-efficient. Use for count/filter/search questions on stored lists (e.g. "how many contacts have email?" → keyword="@"; "find everyone from Tel Aviv" → keyword="תל אביב"). Prefer over read_note whenever you only need to count or filter.
- read_note(filepath): read the full content of a vault file. Use only when you need to see the whole document (e.g. summarize it). Call list_memory first if unsure of the filepath.
- list_memory: show categories + entries the vault contains.
- hide_fact(category, entity): soft-delete a fact (information remains in the vault, just stops surfacing).
- hide_rule(instruction): strike through a rule line by matching its text.

When the user says *"remember that…"* or *"note that…"* — save it immediately as a fact or rule (whichever fits best) and confirm with the file path."""

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
    now = datetime.now(tz=tz).strftime(f"%A, %d %B %Y, %H:%M ({USER_TIMEZONE})")
    addendum = _WEB_FORMAT if chat_id.startswith("web") else _WA_FORMAT
    prompt = (_SYSTEM_BASE + addendum).format(datetime=now)
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
    from app.google.tools import get_google_tools
    from app.tuya.tools import get_tuya_tools
    from app.memory.manager import MEMORY_TOOLS
    from app.web.tools import WEB_TOOLS

    chat_id = state.get("chat_id", "")
    tools = WEB_TOOLS + get_google_tools(chat_id) + get_tuya_tools() + MEMORY_TOOLS

    llm = get_gemini_llm().bind_tools(tools)
    system = _build_system_prompt(state.get("memory_context", ""), state.get("chat_id", ""))

    messages = [SystemMessage(content=system)] + _sanitize_for_gemini(state["messages"])

    response = await llm.ainvoke(messages)
    try:
        response.additional_kwargs["ts"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        pass
    logger.info("Agent response for chat_id=%s tool_calls=%s", chat_id, bool(getattr(response, "tool_calls", None)))
    return {"messages": [response]}

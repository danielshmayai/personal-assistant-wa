"""
Auto-generates System/Capabilities.md in the Obsidian vault on every startup.

The document is built by introspecting live tool definitions + static sections,
so it stays current automatically whenever tools are added or changed.
"""

from datetime import datetime, timezone
from pathlib import Path
from app.memory.obsidian import VAULT_ROOT

_CAPABILITIES_PATH = VAULT_ROOT / "System" / "Capabilities.md"


# ---------------------------------------------------------------------------
# Static sections (update these alongside major behavioral changes)
# ---------------------------------------------------------------------------

_INTRO = """\
I am **danidin**, your personal AI assistant. I run 24/7, remember everything
you tell me, learn from every conversation, and can act on your behalf across
the web, your inbox, your calendar, your files, and your smart home.

Ask me anything in natural language — in Hebrew or English. I will figure out
the right tool to use, chain multiple steps together when needed, and always
remember what I learn about you.
"""

_HOW_I_LEARN = """\
- **Every conversation**: After each reply I silently analyse our exchange for
  corrections, preferences, positive signals, and personal facts — and save
  them to your vault without you having to say "remember this".
- **Implicit signals**: If you rephrase a question or ask for more detail, I
  detect that the previous answer missed the mark and improve my approach.
- **Nightly self-review**: At 3 AM I analyse the past 24 h of conversations,
  surface behavioural patterns, and auto-save strong new rules to your vault.
- **Semantic memory**: Your Obsidian vault is indexed with vector embeddings —
  the most relevant facts surface automatically for every message, not just
  keyword matches.
- **Episodic memory**: Every conversation is summarised and stored so I can
  recall "we talked about X two weeks ago" in future sessions.
- **Rule deduplication**: When a new rule is semantically similar to an
  existing one, the old rule is retired and replaced — no stale contradictions.
"""

_BEST_PRACTICES = """\
### Getting the most out of memory
- Just mention things naturally: "My sister Maya lives in Haifa" → saved automatically.
- Say "remember that…" or "note that…" to force-save something important.
- Say "forget X" or "hide X" to soft-delete facts you no longer want surfaced.
- Ask "what do you know about me?" to see everything stored.

### Google integrations
- Say "connect Google" once to authorise Gmail, Calendar, and Drive together.
- For emails: "show my last 5 emails", "send X an email about Y".
- For calendar: "what's on my schedule?", "book a call with X on Thursday at 3pm".
- For Drive: forward photos/documents and I save them automatically to organised folders.

### Smart home
- Name your devices by their Tuya name: "turn off the living room light",
  "set the AC to 22 degrees", "show me all devices".

### Nutrition & water tracking
- Log meals naturally: "אכלתי חזה עוף עם אורז", "log my lunch: tuna salad".
  I estimate protein, carbs, calories and micronutrients automatically.
- Log water: "שתיתי כוס מים", "drank 500 ml". Daily target is 2 litres.
- Ask for your daily summary: "כמה חלבון אכלתי היום?", "what did I eat today?".
- Snap a photo of your meal and upload it — I'll recognise and log it.

### Google Drive & images
- Forward any photo or document to me and I save it to Drive automatically.
- Ask me to show a saved image: "show me the photo from last week" → it appears inline.
- Browse Drive: "show me my PDFs", "list files in Photos/2026-05".

### 🏋️ Fitness & training
- Log workouts naturally: "עשיתי לג פרס 3×12 80קג RPE 7, 45 דקות". I parse exercises, compute volume, and give progressive-overload targets automatically.
- Ask for a workout plan: "תציע לי אימון כוח של 30 דקות" → I design supersets adapted to your history.
- Log body metrics: "שקלתי 71.5 קג" or "אחוז שומן 21%".
- Ask for your weekly summary: "כמה אימנתי השבוע?" or use the morning fitness brief at 7:45.
- Upload a Garmin/Apple Health screenshot and I'll extract the workout data via OCR.
- Medical constraints (Gilbert's Syndrome + neck/scapula) are always respected — I never recommend contraindicated exercises.

### Web & research
- I browse the web proactively — never say "I can't check that online", just ask.
- For deep research: "search for X and summarise the key points".
- Paste any URL — I'll read and summarise the page.

### Self-improvement loop
- Correct me freely: "no, I meant…", "don't do that again", "from now on…".
  Every correction is stored as a rule immediately.
- Positive feedback works too: "perfect, always do it like this" saves a rule.
- Run `POST /admin/self-review` (or wait for 3 AM) to trigger a manual
  review of recent conversations and flush new insights to your vault.
"""


# ---------------------------------------------------------------------------
# Recent changes (written by deploy workflow, baked into Docker image)
# ---------------------------------------------------------------------------

def _load_recent_changes() -> str:
    """Read recent_changes.json generated at deploy time and return a bullet list."""
    import json
    from pathlib import Path
    path = Path(__file__).parent / "recent_changes.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data:
            return ""
        if isinstance(data, dict):
            data = [data]
        lines = []
        for entry in data:
            if isinstance(entry, str):
                lines.append(f"- {entry}")
            else:
                date = entry.get("date", "")
                msg = entry.get("message", "")
                lines.append(f"- **{date}** — {msg}")
        return "\n".join(lines)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Tool introspection
# ---------------------------------------------------------------------------

def _first_line(desc: str) -> str:
    """Return the first non-empty line of a docstring."""
    for line in (desc or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _tool_rows(tools: list) -> str:
    lines = []
    for t in tools:
        name = getattr(t, "name", str(t))
        desc = _first_line(getattr(t, "description", ""))
        lines.append(f"- **{name}**: {desc}")
    return "\n".join(lines)


def _build_tools_section() -> str:
    sections: list[str] = []

    # Web tools
    try:
        from app.web.tools import WEB_TOOLS
        sections.append("### 🌐 Web & Search\n" + _tool_rows(WEB_TOOLS))
    except Exception:
        pass

    # Google tools (use empty chat_id for introspection — closures still expose .name / .description)
    try:
        from app.google.tools import get_google_tools
        sections.append("### 📧 Google — Gmail · Calendar · Drive\n" + _tool_rows(get_google_tools("")))
    except Exception:
        pass

    # Maps tools
    try:
        from app.google.maps_tool import get_maps_tools
        sections.append("### 🗺️ Maps\n" + _tool_rows(get_maps_tools("")))
    except Exception:
        pass

    # Tuya / Smart home
    try:
        from app.tuya.tools import get_tuya_tools
        tuya = get_tuya_tools()
        if tuya:
            sections.append("### 🏠 Smart Home (Tuya)\n" + _tool_rows(tuya))
    except Exception:
        pass

    # Memory tools
    try:
        from app.memory.manager import MEMORY_TOOLS
        sections.append("### 🧠 Memory & Vault\n" + _tool_rows(MEMORY_TOOLS))
    except Exception:
        pass

    # Schedule tools
    try:
        from app.schedule_tool import get_schedule_tools
        sections.append("### ⏰ Scheduled Actions & Reminders\n" + _tool_rows(get_schedule_tools("")))
    except Exception:
        pass

    # Nutrition tools
    try:
        from app.nutrition_tool import get_nutrition_tools
        sections.append("### 🥗 Nutrition & Water Tracking\n" + _tool_rows(get_nutrition_tools("")))
    except Exception:
        pass

    # Fitness tools
    try:
        from app.fitness_tool import get_fitness_tools
        sections.append("### 🏋️ Fitness & Training\n" + _tool_rows(get_fitness_tools("")))
    except Exception:
        pass

    # TTS tool
    try:
        from app.tts_tool import TTS_TOOLS
        if TTS_TOOLS:
            sections.append("### 🔊 Voice & TTS\n" + _tool_rows(TTS_TOOLS))
    except Exception:
        pass

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_capabilities_doc() -> str:
    """Build and return the full capabilities Markdown document."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tools_section = _build_tools_section()
    recent = _load_recent_changes()
    whats_new_section = (
        f"## What's New\n\n{recent}\n\n"
        if recent
        else ""
    )

    return f"""\
---
type: capabilities
auto_generated: true
updated: {now}
---

# What I Can Do — PA Capabilities Guide

> Auto-generated on startup from live tool definitions. Always up to date.

## What I Am

{_INTRO}

{whats_new_section}## Available Tools

{tools_section}

## How I Learn & Improve

{_HOW_I_LEARN}

## Best Practices

{_BEST_PRACTICES}
"""


def sync_capabilities() -> None:
    """Write (or overwrite) System/Capabilities.md in the vault."""
    try:
        doc = generate_capabilities_doc()
        _CAPABILITIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CAPABILITIES_PATH.write_text(doc, encoding="utf-8", newline="\n")
    except Exception as exc:
        # Non-fatal — vault might not be mounted yet during tests
        import logging
        logging.getLogger("pa.capabilities").warning("Could not write capabilities doc: %s", exc)

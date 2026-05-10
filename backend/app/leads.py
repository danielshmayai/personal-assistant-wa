"""
Lead tracking — silent WhatsApp listener for watched contacts.

Flow:
  1. User adds a phone number via the webapp (label + optional Obsidian note path).
  2. Webhook silently logs messages from/to that contact with no reply.
  3. After each message an LLM pass extracts structured tenant fields and
     updates both the DB and the linked Obsidian note.
  4. User can query the PA about any lead and get full context + next steps.
"""

import asyncio
import json
import logging
import re

from app.memory.store import _get_conn

logger = logging.getLogger("pa.leads")


# ── Phone normalisation ───────────────────────────────────────────────────────

def phone_to_jid(phone: str) -> str:
    """Strip non-digits and append @c.us to form a WhatsApp JID."""
    digits = re.sub(r"\D", "", phone)
    return f"{digits}@c.us"


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_all_leads() -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, chat_id, phone, label, obsidian_note,
                       extracted_data, created_at, last_activity
                FROM watched_leads ORDER BY created_at DESC
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def add_lead(phone: str, label: str, obsidian_note: str) -> dict:
    chat_id = phone_to_jid(phone)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO watched_leads (chat_id, phone, label, obsidian_note)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE
                  SET label = EXCLUDED.label,
                      obsidian_note = EXCLUDED.obsidian_note
                RETURNING id, chat_id, phone, label, obsidian_note, created_at
            """, (chat_id, phone, label, obsidian_note))
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            conn.commit()
            return dict(zip(cols, row))
    finally:
        conn.close()


def delete_lead(lead_id: int) -> bool:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM watched_leads WHERE id = %s", (lead_id,))
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
    finally:
        conn.close()


def get_watched_chat_ids() -> set[str]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id FROM watched_leads")
            return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def log_message(chat_id: str, direction: str, text: str) -> None:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lead_messages (chat_id, direction, text) VALUES (%s, %s, %s)",
                (chat_id, direction, text),
            )
            cur.execute(
                "UPDATE watched_leads SET last_activity = NOW() WHERE chat_id = %s",
                (chat_id,),
            )
            conn.commit()
    finally:
        conn.close()


def get_recent_messages(chat_id: str, limit: int = 40) -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT direction, text, ts FROM lead_messages
                WHERE chat_id = %s ORDER BY ts DESC LIMIT %s
            """, (chat_id, limit))
            rows = cur.fetchall()
            return [{"direction": r[0], "text": r[1], "ts": r[2].isoformat()} for r in reversed(rows)]
    finally:
        conn.close()


def update_extracted_data(chat_id: str, data: dict) -> None:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE watched_leads
                SET extracted_data = extracted_data || %s::jsonb
                WHERE chat_id = %s
            """, (json.dumps(data), chat_id))
            conn.commit()
    finally:
        conn.close()


def get_lead_by_chat_id(chat_id: str) -> dict | None:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, chat_id, phone, label, obsidian_note, extracted_data
                FROM watched_leads WHERE chat_id = %s
            """, (chat_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
    finally:
        conn.close()


# ── In-memory cache (avoid a DB hit on every webhook message) ─────────────────

_watched_cache: set[str] = set()
_cache_loaded = False


def _ensure_cache() -> None:
    global _cache_loaded
    if not _cache_loaded:
        _watched_cache.update(get_watched_chat_ids())
        _cache_loaded = True


def is_watched(chat_id: str) -> bool:
    _ensure_cache()
    return chat_id in _watched_cache


def invalidate_cache() -> None:
    global _cache_loaded
    _cache_loaded = False
    _watched_cache.clear()


# ── LLM extraction ────────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """\
You are analyzing a WhatsApp conversation between a landlord (outbound) and a potential tenant (inbound).

Extract the following fields. Return ONLY valid JSON with these exact keys:
- name: tenant's full name (string or null)
- budget: monthly rent budget as a number (number or null)
- move_in_date: desired move-in date as "YYYY-MM-DD" (string or null)
- num_people: number of people in household (number or null)
- pets: "yes", "no", or null
- employment: "employed", "self-employed", "student", or null
- requirements: comma-separated list of requirements they mentioned (string or null)
- interest_level: "hot", "warm", or "cold" based on their engagement and tone
- notes: any other important details worth remembering (string or null)

Only include fields where you have clear evidence. Use null for anything unknown.

Conversation:
{conversation}

Return JSON only."""


async def extract_and_save(chat_id: str) -> None:
    """Run LLM extraction on recent messages, update DB and Obsidian."""
    try:
        messages = get_recent_messages(chat_id, limit=40)
        if not messages:
            return

        conv_lines = []
        for m in messages:
            role = "Landlord" if m["direction"] == "outbound" else "Tenant"
            conv_lines.append(f"{role}: {m['text']}")
        conversation = "\n".join(conv_lines)

        from app.llm import get_smart_llm
        from langchain_core.messages import HumanMessage

        llm = get_smart_llm()
        response = await llm.ainvoke([HumanMessage(content=_EXTRACT_PROMPT.format(conversation=conversation))])

        raw = response.content.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)

        update_extracted_data(chat_id, data)

        lead = get_lead_by_chat_id(chat_id)
        if lead:
            await _update_obsidian(lead, data)

        logger.info("Lead extraction done for %s: interest=%s", chat_id, data.get("interest_level"))

    except Exception:
        logger.exception("Lead extraction failed for %s", chat_id)


async def _update_obsidian(lead: dict, data: dict) -> None:
    from app.memory import obsidian

    lines = []
    if data.get("name"):
        lines.append(f"**Name:** {data['name']}")
    if data.get("budget"):
        lines.append(f"**Budget:** {data['budget']}")
    if data.get("move_in_date"):
        lines.append(f"**Move-in:** {data['move_in_date']}")
    if data.get("num_people"):
        lines.append(f"**People:** {data['num_people']}")
    if data.get("pets"):
        lines.append(f"**Pets:** {data['pets']}")
    if data.get("employment"):
        lines.append(f"**Employment:** {data['employment']}")
    if data.get("requirements"):
        lines.append(f"**Requirements:** {data['requirements']}")
    if data.get("interest_level"):
        lines.append(f"**Interest:** {data['interest_level']}")
    if data.get("notes"):
        lines.append(f"**Notes:** {data['notes']}")

    summary = "\n".join(lines)
    phone = lead.get("phone", "")
    label = lead.get("label") or phone
    note_path = lead.get("obsidian_note", "")

    loop = asyncio.get_event_loop()

    if note_path:
        content = f"\n### Lead: {phone} — {label}\n{summary}\n"
        await loop.run_in_executor(None, obsidian.append_to_note, note_path, content, "Leads")
    else:
        entity = data.get("name") or phone
        content = f"Potential tenant for: {label}\nPhone: {phone}\n{summary}"
        await loop.run_in_executor(None, obsidian.save_fact, "People", entity, content)


# ── Public entry point called by the webhook ──────────────────────────────────

async def log_and_extract(chat_id: str, direction: str, text: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, log_message, chat_id, direction, text)
    asyncio.create_task(extract_and_save(chat_id))

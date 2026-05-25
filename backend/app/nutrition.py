"""Nutrition tracker — DB helpers + Gemini macro/micro extraction + daily aggregation.

Table: nutrition_logs
  id, chat_id, log_date (DATE in USER_TIMEZONE), meal_description,
  protein, carbs, calories (NUMERIC grams/kcal),
  micros (JSONB — flexible {nutrient_unit: amount}, e.g. {"fiber_g": 6, "iron_mg": 2.1}),
  source ('image' | 'text'), created_at

Macros (protein/carbs/calories) are first-class columns so the dashboard rings and
history graph stay fast. Everything else (vitamins, minerals, fat, fiber, sugar, …)
lives in the flexible `micros` JSONB so Gemini can return whatever it can estimate.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

from app.config import DATABASE_URL, USER_TIMEZONE

logger = logging.getLogger("pa.nutrition")

# Daily protein goal — the one fixed target from the spec.
PROTEIN_TARGET_G = 110

# Strict-JSON contract enforced on every Gemini parse.
_PARSE_INSTRUCTIONS = """\
You are a nutrition estimator. Given a meal (image and/or text), estimate its nutrition.
Respond with ONLY a single JSON object — no markdown, no code fences, no commentary.

Schema (all numeric values are plain numbers, no units inside the value):
{
  "meal_description": "<short description IN HEBREW>",
  "protein": <grams, number>,
  "carbs": <grams, number>,
  "calories": <kcal, number>,
  "micros": {
    "<nutrient>_<unit>": <number>, ...
  }
}

Rules:
- meal_description MUST be in Hebrew.
- protein, carbs, calories are required numbers (use 0 if truly none).
- micros is a flat object of micronutrients you can estimate. Use unit-suffixed keys
  with units g | mg | ug, e.g. "fiber_g", "sugar_g", "fat_g", "saturated_fat_g",
  "sodium_mg", "potassium_mg", "iron_mg", "calcium_mg", "magnesium_mg", "zinc_mg",
  "vitamin_c_mg", "vitamin_d_ug", "vitamin_a_ug", "vitamin_b12_ug".
- Include only micros you can reasonably estimate; omit the rest. micros may be {}.
- Estimate for the whole portion described."""


# ── DB init ─────────────────────────────────────────────────────────────────────

def init_table() -> None:
    if not DATABASE_URL:
        return
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS nutrition_logs (
                    id               SERIAL PRIMARY KEY,
                    chat_id          TEXT NOT NULL,
                    log_date         DATE NOT NULL,
                    meal_description TEXT NOT NULL,
                    protein          NUMERIC NOT NULL DEFAULT 0,
                    carbs            NUMERIC NOT NULL DEFAULT 0,
                    calories         NUMERIC NOT NULL DEFAULT 0,
                    micros           JSONB NOT NULL DEFAULT '{}'::jsonb,
                    source           TEXT NOT NULL DEFAULT 'text',
                    created_at       TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_nutrition_chat_date "
                "ON nutrition_logs (chat_id, log_date)"
            )
            # Migrate rows written before _nutrition_key() normalisation was added.
            # Any chat_id that starts with 'web' but isn't exactly 'web' gets collapsed.
            cur.execute(
                "UPDATE nutrition_logs SET chat_id = 'web' "
                "WHERE chat_id LIKE 'web\\_%' ESCAPE '\\'"
            )
            migrated = cur.rowcount
            if migrated:
                logger.info("nutrition init_table: migrated %d old web_* rows to 'web'", migrated)
        conn.commit()
    finally:
        conn.close()


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _nutrition_key(chat_id: str) -> str:
    """Normalize chat_id so all web sessions share one nutrition store.

    WhatsApp chat_ids are phone numbers (e.g. '972501234567@c.us') and are already
    canonical per-user.  Web sessions use 'web_<uuid>' — strip the UUID so every
    browser/device for the same user writes to the same partition key 'web'.
    """
    if (chat_id or "").startswith("web"):
        return "web"
    return chat_id or "default"


def _user_today() -> date:
    return datetime.now(tz=ZoneInfo(USER_TIMEZONE)).date()


def _num(v) -> float:
    """Coerce a value to a non-negative float, tolerating strings like '12g'."""
    if isinstance(v, (int, float)):
        return max(0.0, float(v))
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v)
        if m:
            return max(0.0, float(m.group()))
    return 0.0


def _clean_micros(raw) -> dict:
    """Keep only numeric micro values keyed by simple strings."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        val = _num(v)
        if val > 0:
            out[k.strip().lower()] = round(val, 2)
    return out


def _merge_micros(dicts: list[dict]) -> dict:
    total: dict[str, float] = {}
    for d in dicts:
        for k, v in (d or {}).items():
            total[k] = round(total.get(k, 0.0) + _num(v), 2)
    return total


# ── DB CRUD ─────────────────────────────────────────────────────────────────────

def insert_log(
    chat_id: str,
    meal_description: str,
    protein: float,
    carbs: float,
    calories: float,
    micros: dict,
    source: str = "text",
    log_date: date | None = None,
) -> int:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO nutrition_logs
                  (chat_id, log_date, meal_description, protein, carbs, calories, micros, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                RETURNING id
                """,
                (
                    _nutrition_key(chat_id),
                    log_date or _user_today(),
                    meal_description,
                    _num(protein),
                    _num(carbs),
                    _num(calories),
                    json.dumps(_clean_micros(micros), ensure_ascii=False),
                    source,
                ),
            )
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id
    finally:
        conn.close()


def list_today(chat_id: str) -> dict:
    """Today's meals + summed totals (macros + merged micros) for *chat_id*."""
    today = _user_today()
    result: dict = {
        "date": today.isoformat(),
        "protein_target": PROTEIN_TARGET_G,
        "meals": [],
        "totals": {"protein": 0.0, "carbs": 0.0, "calories": 0.0, "micros": {}},
    }
    if not DATABASE_URL:
        return result
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, meal_description, protein, carbs, calories, micros, source, created_at
                FROM nutrition_logs
                WHERE chat_id = %s AND log_date = %s
                ORDER BY created_at
                """,
                (_nutrition_key(chat_id), today),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    meals = []
    for r in rows:
        meals.append({
            "id": r["id"],
            "meal_description": r["meal_description"],
            "protein": float(r["protein"]),
            "carbs": float(r["carbs"]),
            "calories": float(r["calories"]),
            "micros": r["micros"] or {},
            "source": r["source"],
            "created_at": r["created_at"].isoformat(),
        })
    result["meals"] = meals
    result["totals"] = {
        "protein": round(sum(m["protein"] for m in meals), 1),
        "carbs": round(sum(m["carbs"] for m in meals), 1),
        "calories": round(sum(m["calories"] for m in meals), 1),
        "micros": _merge_micros([m["micros"] for m in meals]),
    }
    return result


def delete_log(log_id: int, chat_id: str) -> bool:
    if not DATABASE_URL:
        return False
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM nutrition_logs WHERE id = %s AND chat_id = %s",
                (log_id, _nutrition_key(chat_id)),
            )
            affected = cur.rowcount
        conn.commit()
        return affected > 0
    finally:
        conn.close()


def history(chat_id: str, days: int = 14) -> list[dict]:
    """Per-day aggregates for the last *days* days (most recent first)."""
    if not DATABASE_URL:
        return []
    since = _user_today() - timedelta(days=max(1, days) - 1)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT log_date,
                       SUM(protein)  AS protein,
                       SUM(carbs)    AS carbs,
                       SUM(calories) AS calories,
                       COUNT(*)      AS meals
                FROM nutrition_logs
                WHERE chat_id = %s AND log_date >= %s
                GROUP BY log_date
                ORDER BY log_date DESC
                """,
                (_nutrition_key(chat_id), since),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "date": r[0].isoformat(),
            "protein": round(float(r[1]), 1),
            "carbs": round(float(r[2]), 1),
            "calories": round(float(r[3]), 1),
            "meals": int(r[4]),
        }
        for r in rows
    ]


# ── Gemini parsing ────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of an LLM response, tolerating code fences."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object in model response: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def _normalize(parsed: dict) -> dict:
    return {
        "meal_description": str(parsed.get("meal_description") or "ארוחה").strip(),
        "protein": _num(parsed.get("protein")),
        "carbs": _num(parsed.get("carbs")),
        "calories": _num(parsed.get("calories")),
        "micros": _clean_micros(parsed.get("micros")),
    }


async def parse_meal_text(text: str) -> dict:
    """Estimate macros + micros from a textual meal description via Gemini."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import get_gemini_llm

    llm = get_gemini_llm()
    messages = [
        SystemMessage(content=_PARSE_INSTRUCTIONS),
        HumanMessage(content=f"Meal description:\n{text}"),
    ]
    resp = await llm.ainvoke(messages)
    return _normalize(_extract_json(resp.content if hasattr(resp, "content") else str(resp)))


async def parse_meal_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """Estimate macros + micros from a plate photo via Gemini vision."""
    import base64

    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import get_gemini_llm

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_uri = f"data:{mime_type or 'image/jpeg'};base64,{b64}"
    llm = get_gemini_llm()
    messages = [
        SystemMessage(content=_PARSE_INSTRUCTIONS),
        HumanMessage(content=[
            {"type": "text", "text": "Estimate the nutrition of the meal in this image."},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]),
    ]
    resp = await llm.ainvoke(messages)
    return _normalize(_extract_json(resp.content if hasattr(resp, "content") else str(resp)))

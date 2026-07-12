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
WATER_TARGET_ML  = 2000

# Strict-JSON contract enforced on every Gemini parse.
_PARSE_INSTRUCTIONS = """\
You are a nutrition estimator. Given a meal (image and/or text), estimate its nutrition.
Respond with ONLY a single JSON object — no markdown, no code fences, no commentary.

Schema (all numeric values are plain numbers, no units inside the value):
{
  "meal_description": "<full description of all components IN HEBREW, e.g. 'חזה עוף על הגריל, אורז לבן, סלט ירקות עם שמן זית ולימון'>",
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
        conn.commit()
    finally:
        conn.close()


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _nutrition_key(_chat_id: str = "") -> str:
    """Data scope key. The owner (no tenant context, engine_scope '') shares the
    legacy 'default' store across WhatsApp + web; every other tenant is isolated
    under its own id. The chat_id arg is vestigial — the real scope comes from
    the current_tenant_id ContextVar set by the product's require_session."""
    from app.context import current_tenant_id
    return current_tenant_id.get() or "default"


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

    all_rows = []
    meals = []
    for r in rows:
        entry = {
            "id": r["id"],
            "meal_description": r["meal_description"],
            "protein": float(r["protein"]),
            "carbs": float(r["carbs"]),
            "calories": float(r["calories"]),
            "micros": r["micros"] or {},
            "source": r["source"],
            "created_at": r["created_at"].isoformat(),
        }
        all_rows.append(entry)
        if r["source"] != "water":
            meals.append(entry)

    merged_micros = _merge_micros([r["micros"] for r in all_rows])
    water_ml = int(merged_micros.pop("water_ml", 0))

    result["meals"] = meals
    result["water_ml"] = water_ml
    result["water_target_ml"] = WATER_TARGET_ML
    result["totals"] = {
        "protein": round(sum(m["protein"] for m in meals), 1),
        "carbs": round(sum(m["carbs"] for m in meals), 1),
        "calories": round(sum(m["calories"] for m in meals), 1),
        "micros": merged_micros,
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
                       SUM(protein)  FILTER (WHERE source != 'water') AS protein,
                       SUM(carbs)    FILTER (WHERE source != 'water') AS carbs,
                       SUM(calories) FILTER (WHERE source != 'water') AS calories,
                       COUNT(*)      FILTER (WHERE source != 'water') AS meals,
                       SUM(COALESCE((micros->>'water_ml')::numeric, 0))
                         FILTER (WHERE source = 'water')              AS water_ml
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
            "protein": round(float(r[1] or 0), 1),
            "carbs": round(float(r[2] or 0), 1),
            "calories": round(float(r[3] or 0), 1),
            "meals": int(r[4] or 0),
            "water_ml": int(r[5] or 0),
        }
        for r in rows
    ]


def log_water(chat_id: str, amount_ml: int, log_date: date | None = None) -> int:
    """Record a water drink. Stored as a zero-calorie entry with micros.water_ml."""
    ml = max(1, int(amount_ml))
    return insert_log(
        _nutrition_key(chat_id),
        f"מים ({ml} מ\"ל)",
        protein=0, carbs=0, calories=0,
        micros={"water_ml": ml},
        source="water",
        log_date=log_date,
    )


def water_today(chat_id: str) -> int:
    """Total water in ml logged today for *chat_id*."""
    data = list_today(chat_id)
    return int(data["totals"]["micros"].get("water_ml", 0))


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


async def suggest_meals(chat_id: str) -> dict:
    """Return 2-3 meal suggestions to close today's remaining nutritional gap.

    Factors in: current time-of-day (meal type), protein still needed,
    carbs already eaten (goal: keep low), and total calories so far.
    Returns structured JSON so the UI can render it as cards.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import get_gemini_llm
    from app.config import USER_TIMEZONE
    from zoneinfo import ZoneInfo
    from datetime import datetime

    tz = ZoneInfo(USER_TIMEZONE)
    now = datetime.now(tz=tz)
    hour = now.hour
    time_str = now.strftime("%H:%M")

    if hour < 10:
        meal_context = "בוקר"
    elif hour < 13:
        meal_context = "ארוחת ביניים בוקר / צהריים מוקדם"
    elif hour < 16:
        meal_context = "צהריים"
    elif hour < 19:
        meal_context = "ארוחת ביניים אחה\"צ"
    else:
        meal_context = "ערב"

    today = list_today(chat_id)
    totals = today["totals"]
    protein_done = round(totals["protein"], 1)
    carbs_done = round(totals["carbs"], 1)
    cals_done = round(totals["calories"], 1)
    protein_left = max(0, PROTEIN_TARGET_G - protein_done)
    meals_eaten = [m["meal_description"] for m in today["meals"]]

    facts = {
        "current_time": time_str,
        "meal_occasion": meal_context,
        "protein_eaten_g": protein_done,
        "protein_target_g": PROTEIN_TARGET_G,
        "protein_still_needed_g": protein_left,
        "carbs_eaten_g": carbs_done,
        "calories_eaten_kcal": cals_done,
        "meals_eaten_today": meals_eaten,
    }

    instructions = """\
You are a personal nutrition coach. Based on the daily intake so far and the current time,
suggest 2-3 specific meals or snacks (appropriate for the meal occasion) to help reach
the daily protein target while keeping carbs low.

Respond with ONLY a valid JSON object — no markdown, no code fences.

Schema:
{
  "intro": "<1 sentence Hebrew intro summarising the gap, e.g. 'נשאר לך 25 גרם חלבון להשלים — הנה הצעות לארוחת ערב:'>",
  "suggestions": [
    {
      "name": "<meal name in Hebrew>",
      "description": "<Hebrew, 1-2 sentences: what it contains and why it fits>",
      "est_protein_g": <number>,
      "est_carbs_g": <number>,
      "est_calories_kcal": <number>
    }
  ]
}

Rules:
- Exactly 2-3 suggestions.
- Prioritise high-protein, low-carb options (lean meat, eggs, cottage, Greek yogurt, tuna, tofu).
- Portion sizes and content must be realistic and practical.
- If protein_still_needed_g < 10, suggest light protein-rich snacks only.
- If it is late evening (after 21:00), suggest lighter options.
- All text (name, description, intro) must be in Hebrew."""

    llm = get_gemini_llm()
    messages = [
        SystemMessage(content=instructions),
        HumanMessage(content=f"Today's intake so far:\n{json.dumps(facts, ensure_ascii=False, indent=2)}"),
    ]
    resp = await llm.ainvoke(messages)
    raw = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
    return {
        "intro": str(raw.get("intro") or ""),
        "suggestions": [
            {
                "name": str(s.get("name") or ""),
                "description": str(s.get("description") or ""),
                "est_protein_g": _num(s.get("est_protein_g")),
                "est_carbs_g": _num(s.get("est_carbs_g")),
                "est_calories_kcal": _num(s.get("est_calories_kcal")),
            }
            for s in (raw.get("suggestions") or [])[:3]
            if s.get("name")
        ],
        "protein_left": protein_left,
        "meal_occasion": meal_context,
    }

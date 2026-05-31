"""Fitness tracker — DB helpers + Gemini workout parsing + daily aggregation.

Tables:
  fitness_workouts:     id, chat_id, log_date, workout_type, title, duration_min,
                        avg_rpe, total_volume, exercises (JSONB), metrics (JSONB),
                        ai_summary, ai_next_rec (JSONB), source, created_at
  fitness_body_metrics: id, chat_id, log_date, weight_kg, lbm_kg, smm_kg, bf_pct,
                        source, note, created_at
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

logger = logging.getLogger("pa.fitness")


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _fitness_key(_chat_id: str) -> str:
    """Single-user PA — all sources share one fitness store."""
    return "default"


def _user_today() -> date:
    return datetime.now(tz=ZoneInfo(USER_TIMEZONE)).date()


def _num(v) -> float:
    if isinstance(v, (int, float)):
        return max(0.0, float(v))
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v)
        if m:
            return max(0.0, float(m.group()))
    return 0.0


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object in model response: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def _clean_exercises(raw) -> list:
    """Normalise the exercises list — only keep dicts with at least a name."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "sets": int(_num(item.get("sets"))),
            "reps": int(_num(item.get("reps"))),
            "weight_kg": round(_num(item.get("weight_kg")), 1),
            "rpe": round(_num(item.get("rpe")), 1),
            "duration_sec": int(_num(item.get("duration_sec"))),
            "notes": str(item.get("notes") or ""),
        })
    return out


def _total_volume(exercises: list) -> float:
    """Σ sets × reps × weight_kg for all exercises."""
    total = 0.0
    for ex in exercises:
        s = _num(ex.get("sets"))
        r = _num(ex.get("reps"))
        w = _num(ex.get("weight_kg"))
        total += s * r * w
    return round(total, 1)


# ── DB init ─────────────────────────────────────────────────────────────────────

def init_table() -> None:
    if not DATABASE_URL:
        return
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fitness_workouts (
                    id            SERIAL PRIMARY KEY,
                    chat_id       TEXT NOT NULL,
                    log_date      DATE NOT NULL,
                    workout_type  TEXT NOT NULL DEFAULT 'strength',
                    title         TEXT NOT NULL DEFAULT '',
                    duration_min  NUMERIC NOT NULL DEFAULT 0,
                    avg_rpe       NUMERIC NOT NULL DEFAULT 0,
                    total_volume  NUMERIC NOT NULL DEFAULT 0,
                    exercises     JSONB NOT NULL DEFAULT '[]'::jsonb,
                    metrics       JSONB NOT NULL DEFAULT '{}'::jsonb,
                    ai_summary    TEXT NOT NULL DEFAULT '',
                    ai_next_rec   JSONB NOT NULL DEFAULT '{}'::jsonb,
                    source        TEXT NOT NULL DEFAULT 'text',
                    created_at    TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_fitness_workouts_chat_date "
                "ON fitness_workouts (chat_id, log_date)"
            )
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fitness_body_metrics (
                    id          SERIAL PRIMARY KEY,
                    chat_id     TEXT NOT NULL,
                    log_date    DATE NOT NULL,
                    weight_kg   NUMERIC,
                    lbm_kg      NUMERIC,
                    smm_kg      NUMERIC,
                    bf_pct      NUMERIC,
                    source      TEXT NOT NULL DEFAULT 'manual',
                    note        TEXT NOT NULL DEFAULT '',
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_fitness_body_metrics_chat_date "
                "ON fitness_body_metrics (chat_id, log_date)"
            )
            cur.execute(
                "UPDATE fitness_workouts SET chat_id = 'default' WHERE chat_id != 'default'"
            )
            cur.execute(
                "UPDATE fitness_body_metrics SET chat_id = 'default' WHERE chat_id != 'default'"
            )
        conn.commit()
    finally:
        conn.close()


# ── Workout CRUD ─────────────────────────────────────────────────────────────────

def insert_workout(
    chat_id: str,
    workout_type: str,
    title: str,
    duration_min: float,
    avg_rpe: float,
    exercises: list,
    metrics: dict,
    source: str = "text",
    log_date: date | None = None,
) -> int:
    exercises = _clean_exercises(exercises)
    vol = _total_volume(exercises)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fitness_workouts
                  (chat_id, log_date, workout_type, title, duration_min, avg_rpe,
                   total_volume, exercises, metrics, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                RETURNING id
                """,
                (
                    _fitness_key(chat_id),
                    log_date or _user_today(),
                    workout_type or "strength",
                    title or "",
                    _num(duration_min),
                    _num(avg_rpe),
                    vol,
                    json.dumps(exercises, ensure_ascii=False),
                    json.dumps(metrics or {}, ensure_ascii=False),
                    source,
                ),
            )
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id
    finally:
        conn.close()


def list_today(chat_id: str) -> dict:
    """Today's workouts."""
    today = _user_today()
    result: dict = {"date": today.isoformat(), "workouts": [], "total_volume": 0.0, "total_duration_min": 0.0}
    if not DATABASE_URL:
        return result
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, workout_type, title, duration_min, avg_rpe,
                       total_volume, exercises, metrics, ai_summary, ai_next_rec, source, created_at
                FROM fitness_workouts
                WHERE chat_id = %s AND log_date = %s
                ORDER BY created_at
                """,
                (_fitness_key(chat_id), today),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    workouts = []
    for r in rows:
        workouts.append({
            "id": r["id"],
            "workout_type": r["workout_type"],
            "title": r["title"],
            "duration_min": float(r["duration_min"]),
            "avg_rpe": float(r["avg_rpe"]),
            "total_volume": float(r["total_volume"]),
            "exercises": r["exercises"] or [],
            "metrics": r["metrics"] or {},
            "ai_summary": r["ai_summary"],
            "ai_next_rec": r["ai_next_rec"] or {},
            "source": r["source"],
            "created_at": r["created_at"].isoformat(),
        })

    result["workouts"] = workouts
    result["total_volume"] = round(sum(w["total_volume"] for w in workouts), 1)
    result["total_duration_min"] = round(sum(w["duration_min"] for w in workouts), 1)
    return result


def history(chat_id: str, days: int = 30) -> list[dict]:
    """Per-day aggregates for the last *days* days (most recent first)."""
    if not DATABASE_URL:
        return []
    since = _user_today() - timedelta(days=max(1, days) - 1)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT log_date,
                       SUM(total_volume)   AS total_volume,
                       SUM(duration_min)   AS total_duration,
                       AVG(avg_rpe)        AS avg_rpe,
                       COUNT(*)            AS session_count,
                       array_agg(DISTINCT workout_type) AS types,
                       json_agg(
                         json_build_object(
                           'id', id,
                           'title', title,
                           'workout_type', workout_type,
                           'duration_min', duration_min,
                           'avg_rpe', avg_rpe,
                           'total_volume', total_volume,
                           'ai_next_rec', ai_next_rec,
                           'exercises', exercises
                         ) ORDER BY created_at
                       ) AS sessions
                FROM fitness_workouts
                WHERE chat_id = %s AND log_date >= %s
                GROUP BY log_date
                ORDER BY log_date DESC
                """,
                (_fitness_key(chat_id), since),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "date": r["log_date"].isoformat(),
            "total_volume": round(float(r["total_volume"] or 0), 1),
            "total_duration": round(float(r["total_duration"] or 0), 1),
            "avg_rpe": round(float(r["avg_rpe"] or 0), 1),
            "session_count": int(r["session_count"] or 0),
            "types": list(r["types"] or []),
            "sessions": r["sessions"] or [],
        }
        for r in rows
    ]


def get_workout(workout_id: int, chat_id: str) -> dict | None:
    if not DATABASE_URL:
        return None
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM fitness_workouts WHERE id = %s AND chat_id = %s",
                (workout_id, _fitness_key(chat_id)),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return dict(row)


def delete_workout(workout_id: int, chat_id: str) -> bool:
    if not DATABASE_URL:
        return False
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM fitness_workouts WHERE id = %s AND chat_id = %s",
                (workout_id, _fitness_key(chat_id)),
            )
            affected = cur.rowcount
        conn.commit()
        return affected > 0
    finally:
        conn.close()


def update_workout_ai(workout_id: int, summary: str, next_rec: dict) -> None:
    if not DATABASE_URL:
        return
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE fitness_workouts SET ai_summary = %s, ai_next_rec = %s::jsonb WHERE id = %s",
                (summary, json.dumps(next_rec, ensure_ascii=False), workout_id),
            )
        conn.commit()
    finally:
        conn.close()


# ── Body metrics CRUD ─────────────────────────────────────────────────────────────

def insert_body_metric(
    chat_id: str,
    weight_kg: float | None = None,
    lbm_kg: float | None = None,
    smm_kg: float | None = None,
    bf_pct: float | None = None,
    source: str = "manual",
    note: str = "",
    log_date: date | None = None,
) -> int:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fitness_body_metrics
                  (chat_id, log_date, weight_kg, lbm_kg, smm_kg, bf_pct, source, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    _fitness_key(chat_id),
                    log_date or _user_today(),
                    weight_kg,
                    lbm_kg,
                    smm_kg,
                    bf_pct,
                    source,
                    note or "",
                ),
            )
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id
    finally:
        conn.close()


def latest_body_metrics(chat_id: str) -> dict | None:
    """Most recent body metrics row."""
    if not DATABASE_URL:
        return None
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT weight_kg, lbm_kg, smm_kg, bf_pct, log_date, source
                FROM fitness_body_metrics
                WHERE chat_id = %s
                ORDER BY log_date DESC, created_at DESC
                LIMIT 1
                """,
                (_fitness_key(chat_id),),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        k: (float(v) if v is not None else None)
        if k not in ("log_date", "source") else (v.isoformat() if hasattr(v, "isoformat") else v)
        for k, v in row.items()
    }


def body_metrics_history(chat_id: str, days: int = 180) -> list[dict]:
    if not DATABASE_URL:
        return []
    since = _user_today() - timedelta(days=days)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, log_date, weight_kg, lbm_kg, smm_kg, bf_pct, source, note
                FROM fitness_body_metrics
                WHERE chat_id = %s AND log_date >= %s
                ORDER BY log_date ASC
                """,
                (_fitness_key(chat_id), since),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "log_date": r["log_date"].isoformat(),
            "weight_kg": float(r["weight_kg"]) if r["weight_kg"] is not None else None,
            "lbm_kg": float(r["lbm_kg"]) if r["lbm_kg"] is not None else None,
            "smm_kg": float(r["smm_kg"]) if r["smm_kg"] is not None else None,
            "bf_pct": float(r["bf_pct"]) if r["bf_pct"] is not None else None,
            "source": r["source"],
            "note": r["note"],
        })
    return result


def delete_body_metric(metric_id: int, chat_id: str) -> bool:
    if not DATABASE_URL:
        return False
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM fitness_body_metrics WHERE id = %s AND chat_id = %s",
                (metric_id, _fitness_key(chat_id)),
            )
            affected = cur.rowcount
        conn.commit()
        return affected > 0
    finally:
        conn.close()


def exercise_progression(chat_id: str, exercise_name: str, days: int = 180) -> list[dict]:
    """Per-session top_weight and volume for a given exercise."""
    if not DATABASE_URL:
        return []
    since = _user_today() - timedelta(days=days)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT log_date, exercises
                FROM fitness_workouts
                WHERE chat_id = %s AND log_date >= %s
                  AND exercises::text ILIKE %s
                ORDER BY log_date ASC
                """,
                (_fitness_key(chat_id), since, f"%{exercise_name}%"),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    results = []
    name_lower = exercise_name.lower()
    for log_date, exercises in rows:
        if not exercises:
            continue
        matching = [ex for ex in exercises if name_lower in str(ex.get("name", "")).lower()]
        if not matching:
            continue
        top_weight = max(_num(ex.get("weight_kg")) for ex in matching)
        vol = sum(
            _num(ex.get("sets")) * _num(ex.get("reps")) * _num(ex.get("weight_kg"))
            for ex in matching
        )
        results.append({
            "date": log_date.isoformat(),
            "top_weight": round(top_weight, 1),
            "volume": round(vol, 1),
        })
    return results


# ── LLM parsing ─────────────────────────────────────────────────────────────────

_PARSE_WORKOUT_INSTRUCTIONS = """\
You are a workout parser. Given a workout description (text or image), extract structured data.
Respond with ONLY a single JSON object — no markdown, no code fences, no commentary.

Schema:
{
  "title": "<Hebrew workout title, e.g. 'אימון כוח פלג גוף עליון'>",
  "workout_type": "<one of: strength | hiit | cardio | skiing | running | other>",
  "duration_min": <number>,
  "avg_rpe": <number 1-10, overall effort, 0 if unknown>,
  "exercises": [
    {
      "name": "<exercise name in Hebrew or English>",
      "sets": <int>,
      "reps": <int, 0 if cardio/duration-based>,
      "weight_kg": <number, 0 if bodyweight>,
      "rpe": <number 1-10, 0 if unknown>,
      "duration_sec": <int, 0 if rep-based>,
      "notes": "<optional notes>"
    }
  ],
  "metrics": {
    "hr_avg": <number or null>,
    "hr_max": <number or null>,
    "calories": <number or null>,
    "distance_km": <number or null>,
    "pace_min_km": <number or null>
  }
}

Rules:
- title must be in Hebrew.
- workout_type: infer from context (gym exercises → strength; running/cycling → cardio/running; intervals → hiit; ski/snowboard → skiing).
- avg_rpe: estimate from text, or 0 if not mentioned.
- exercises: include all mentioned exercises. Omit unknown values (use 0).
- metrics: populate from wearable screenshots or text. Use null for unknown fields.
- If no exercises (pure cardio), exercises may be [].
- Do NOT include body metrics (weight, BF%) in this response."""


async def parse_workout_text(text: str, chat_id: str = "") -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import get_gemini_llm
    from app.fitness_profile import render_profile_block

    metrics = latest_body_metrics(chat_id)
    profile = render_profile_block(metrics)

    llm = get_gemini_llm()
    messages = [
        SystemMessage(content=f"{profile}\n\n{_PARSE_WORKOUT_INSTRUCTIONS}"),
        HumanMessage(content=f"Workout description:\n{text}"),
    ]
    resp = await llm.ainvoke(messages)
    raw = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
    return _normalize_workout(raw)


async def parse_workout_image(image_bytes: bytes, mime_type: str = "image/jpeg", chat_id: str = "") -> dict:
    import base64
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import get_gemini_llm
    from app.fitness_profile import render_profile_block

    metrics = latest_body_metrics(chat_id)
    profile = render_profile_block(metrics)

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_uri = f"data:{mime_type or 'image/jpeg'};base64,{b64}"
    llm = get_gemini_llm()
    messages = [
        SystemMessage(content=f"{profile}\n\n{_PARSE_WORKOUT_INSTRUCTIONS}"),
        HumanMessage(content=[
            {"type": "text", "text": "Extract the workout data from this image (e.g. Garmin/Apple Health screenshot or gym note)."},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]),
    ]
    resp = await llm.ainvoke(messages)
    raw = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
    return _normalize_workout(raw)


def _normalize_workout(parsed: dict) -> dict:
    valid_types = {"strength", "hiit", "cardio", "skiing", "running", "other"}
    wtype = str(parsed.get("workout_type") or "strength").lower()
    if wtype not in valid_types:
        wtype = "other"
    metrics_raw = parsed.get("metrics") or {}
    metrics = {
        k: (_num(v) if v is not None else None)
        for k, v in metrics_raw.items()
    }
    return {
        "title": str(parsed.get("title") or "אימון").strip(),
        "workout_type": wtype,
        "duration_min": _num(parsed.get("duration_min")),
        "avg_rpe": _num(parsed.get("avg_rpe")),
        "exercises": _clean_exercises(parsed.get("exercises") or []),
        "metrics": metrics,
    }


_EVALUATE_INSTRUCTIONS = """\
You are a personal trainer AI. Evaluate the completed workout and give progressive-overload recommendations.
Respond with ONLY a single JSON object — no markdown, no code fences.

Schema:
{
  "ai_summary": "<1-2 sentence Hebrew summary of the workout and key observations>",
  "ai_next_rec": {
    "summary": "<1-2 sentence Hebrew recommendation for next session>",
    "focus": "<one of: progressive_overload | deload | maintain | recovery>",
    "targets": [
      {
        "name": "<exercise name>",
        "target_weight_kg": <number>,
        "target_reps": <int>,
        "rationale": "<Hebrew, one sentence>"
      }
    ]
  }
}

Progressive-overload rules (apply per exercise based on RPE):
- RPE ≤ 6: increase weight by 5% next session.
- RPE 7-8: increase weight by 2.5% next session.
- RPE ≥ 9: hold weight, focus on form; if RPE=10 suggest deload.
- Bodyweight exercises (weight_kg=0): increase reps by 1-2.
- Cardio: increase distance by 5% or reduce pace by 5 sec/km.

MANDATORY CONSTRAINTS (always enforce):
- NEVER recommend heavy barbell back squat, conventional deadlift, or military press.
- ALWAYS emphasize scapular stabilizer exercises (face pulls, band pull-aparts, serratus wall slides, prone Y/T/W) if this is a strength session.
- Include a hydration reminder (Gilbert's Syndrome) in ai_summary.
- If avg_rpe ≥ 9, recommend recovery focus in ai_next_rec.focus.
- If neck/shoulder exercises are present, add a neutral-neck form cue."""


async def evaluate_workout(workout: dict, chat_id: str = "") -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import get_gemini_llm
    from app.fitness_profile import render_profile_block

    metrics = latest_body_metrics(chat_id)
    profile = render_profile_block(metrics)

    llm = get_gemini_llm()
    messages = [
        SystemMessage(content=f"{profile}\n\n{_EVALUATE_INSTRUCTIONS}"),
        HumanMessage(content=f"Completed workout:\n{json.dumps(workout, ensure_ascii=False, indent=2)}"),
    ]
    resp = await llm.ainvoke(messages)
    raw = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
    return {
        "ai_summary": str(raw.get("ai_summary") or ""),
        "ai_next_rec": raw.get("ai_next_rec") or {},
    }


async def suggest_workout(
    chat_id: str,
    minutes: int = 45,
    workout_type: str = "strength",
) -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import get_gemini_llm
    from app.fitness_profile import render_profile_block

    recent = history(chat_id, days=14)
    metrics = latest_body_metrics(chat_id)
    profile = render_profile_block(metrics)

    last_targets = ""
    if recent and recent[0].get("sessions"):
        last_session = recent[0]["sessions"][-1]
        rec = last_session.get("ai_next_rec") or {}
        targets = rec.get("targets") or []
        if targets:
            last_targets = "Last session targets:\n" + json.dumps(targets, ensure_ascii=False, indent=2)

    suggest_instructions = f"""\
You are a personal trainer AI. Design a {minutes}-minute {workout_type} workout.
Respond with ONLY a single JSON object — no markdown, no code fences.

Schema:
{{
  "title": "<Hebrew workout title>",
  "workout_type": "{workout_type}",
  "duration_min": {minutes},
  "rationale": "<Hebrew, 1-2 sentences explaining why this workout was chosen today>",
  "exercises": [
    {{
      "name": "<exercise name>",
      "sets": <int>,
      "reps": <int>,
      "weight_kg": <number>,
      "rpe": <target RPE 1-10>,
      "duration_sec": <int, 0 if rep-based>,
      "notes": "<optional superset pairing or cue>"
    }}
  ],
  "supersets": [
    ["<exercise A>", "<exercise B>"]
  ]
}}

Timing guidelines:
- 30 min → 4-5 exercises as 2-3 supersets for maximum efficiency.
- 45 min → 5-6 exercises, 2-3 supersets.
- 60 min → 7-8 exercises, full progressive split.

Use ai_next_rec targets from previous session as weights when available.
MANDATORY CONSTRAINTS (always enforce):
- NEVER include heavy barbell back squat, conventional deadlift, or military press.
- ALWAYS include at least one scapular stabilizer exercise (face pulls, band pull-aparts, prone Y/T/W, serratus wall slides).
- Prefer machine/cable work over heavy free-bar axial loading.
- Include a hydration note in the rationale."""

    llm = get_gemini_llm()
    history_summary = json.dumps(recent[:5], ensure_ascii=False, indent=2) if recent else "No history yet."
    messages = [
        SystemMessage(content=f"{profile}\n\n{suggest_instructions}"),
        HumanMessage(content=f"Recent history (last 5 days):\n{history_summary}\n\n{last_targets}"),
    ]
    resp = await llm.ainvoke(messages)
    raw = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
    exercises = _clean_exercises(raw.get("exercises") or [])
    return {
        "title": str(raw.get("title") or "אימון מוצע"),
        "workout_type": workout_type,
        "duration_min": minutes,
        "rationale": str(raw.get("rationale") or ""),
        "exercises": exercises,
        "supersets": raw.get("supersets") or [],
        "total_volume": _total_volume(exercises),
    }


async def generate_morning_brief(chat_id: str) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import get_gemini_llm
    from app.fitness_profile import render_profile_block, PHYSIO_BASELINE
    from app.config import FITNESS_WEEKLY_SESSION_TARGET

    recent = history(chat_id, days=7)
    metrics = latest_body_metrics(chat_id)
    profile = render_profile_block(metrics)

    last_rec = ""
    if recent and recent[0].get("sessions"):
        last_session = recent[0]["sessions"][-1]
        rec = last_session.get("ai_next_rec") or {}
        last_rec = f"\nLast session recommendation:\n{json.dumps(rec, ensure_ascii=False, indent=2)}"

    instructions = f"""\
You are a personal trainer AI giving a daily morning fitness brief in Hebrew.
Weekly session target: {FITNESS_WEEKLY_SESSION_TARGET} sessions.
Be concise (WhatsApp-friendly, use • bullets, max 200 words).
Include:
1. Weekly compliance (sessions done / target)
2. Recovery status (avg RPE trend, days since last session)
3. Today's recommendation (what type of workout, duration, key focus)
4. Hydration reminder (Gilbert's Syndrome — min 2.5L today)
5. Neck/scapula reminder if strength session is recommended
Format: WhatsApp-friendly (*bold* for headers, • for bullets, no # headers)."""

    llm = get_gemini_llm()
    messages = [
        SystemMessage(content=f"{profile}\n\n{instructions}"),
        HumanMessage(content=f"Last 7 days training:\n{json.dumps(recent, ensure_ascii=False, indent=2)}{last_rec}"),
    ]
    resp = await llm.ainvoke(messages)
    return str(resp.content if hasattr(resp, "content") else str(resp)).strip()

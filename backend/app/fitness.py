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

def _fitness_key(_chat_id: str = "") -> str:
    """Data scope key. The owner (no tenant context, engine_scope '') shares the
    legacy 'default' store across WhatsApp + web; every other tenant is isolated
    under its own id. The chat_id arg is vestigial — the real scope comes from
    the current_tenant_id ContextVar set by the product's require_session."""
    from app.context import current_tenant_id
    return current_tenant_id.get() or "default"


def _garmin_allowed() -> bool:
    """Garmin is a single owner-global account. Never surface its wellness data
    for a non-owner tenant — that would leak the owner's steps/HR/sleep."""
    from app.context import current_tenant_id
    return not current_tenant_id.get()


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
    """Σ sets × reps × weight_kg (rep-based) or sets × (duration_sec/30) × weight_kg (timed)."""
    total = 0.0
    for ex in exercises:
        s = _num(ex.get("sets"))
        r = _num(ex.get("reps"))
        w = _num(ex.get("weight_kg"))
        d = _num(ex.get("duration_sec"))
        if r > 0:
            total += s * r * w
        elif d > 0 and w > 0:
            # Timed + weighted: treat 30 sec as 1 rep equivalent
            total += s * (d / 30.0) * w
    return round(total, 1)


def format_workout_log_message(parsed: dict, evaluation: dict, today: dict, tag: str = "") -> str:
    """Build the '✅ ... נרשם' WhatsApp confirmation shared by every logging path —
    manual/photo chat log_workout AND Garmin auto-import — so a workout looks and reads
    identically in chat no matter where it came from.

    parsed: {title, workout_type, duration_min, avg_rpe, exercises, metrics,
             is_estimated_volume?, user_feedback?}
    evaluation: {ai_summary, ai_next_rec: {targets, ...}}
    today: result of list_today(chat_id) — used for the running daily totals footer.
    tag: optional short suffix appended to the title line, e.g. " ⌚" for watch imports.
    """
    exercises = parsed.get("exercises") or []
    metrics = parsed.get("metrics") or {}
    avg_rpe = _num(parsed.get("avg_rpe"))

    detail = [f"סוג: {parsed.get('workout_type', '')}", f"{_num(parsed.get('duration_min')):.0f} דקות"]
    if avg_rpe:
        detail.append(f"RPE {avg_rpe:.1f}")
    lines = [f"✅ *{parsed.get('title', '')}*{tag} נרשם", "• " + " | ".join(detail)]

    if exercises:
        volume = _total_volume(exercises)
        vol_note = " *(הערכה)*" if parsed.get("is_estimated_volume") else ""
        lines.append(f"• נפח: {volume:.0f} קג{vol_note}")
    else:
        # Cardio / Garmin activity with no exercise list — report what we do have.
        cardio_detail = []
        if metrics.get("distance_km"):
            cardio_detail.append(f"{metrics['distance_km']:.1f} ק\"מ")
        if metrics.get("hr_avg"):
            cardio_detail.append(f"דופק ממוצע {metrics['hr_avg']:.0f}")
        if metrics.get("calories"):
            cardio_detail.append(f"{metrics['calories']:.0f} קלוריות")
        if cardio_detail:
            lines.append("• " + " | ".join(cardio_detail))

    if parsed.get("user_feedback"):
        lines.append(f"\n{parsed['user_feedback']}")
    if evaluation.get("ai_summary"):
        lines.append(f"\n💡 {evaluation['ai_summary']}")
    targets = (evaluation.get("ai_next_rec") or {}).get("targets") or []
    if targets:
        lines.append("\n*יעדים לאימון הבא:*")
        for t in targets[:3]:
            lines.append(f"• {t['name']}: {t.get('target_weight_kg', '?')} קג × {t.get('target_reps', '?')}")
    lines.append(f"\n— סה\"כ היום: {_num(today.get('total_duration_min')):.0f} דקות, נפח {_num(today.get('total_volume')):.0f} קג")
    return "\n".join(lines)


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
            # Body-scan support: extra measurements (BMR, visceral fat, water…) from
            # InBody-style summary sheets + the per-measurement AI trend analysis.
            cur.execute(
                "ALTER TABLE fitness_body_metrics ADD COLUMN IF NOT EXISTS extras JSONB DEFAULT '{}'"
            )
            cur.execute(
                "ALTER TABLE fitness_body_metrics ADD COLUMN IF NOT EXISTS ai_summary TEXT DEFAULT ''"
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
                           'ai_summary', ai_summary,
                           'exercises', exercises,
                           'metrics', metrics,
                           'source', source
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


def merge_garmin_metrics(workout_id: int, garmin_metrics: dict) -> bool:
    """Merge Garmin activity data (HR, calories, activity id) into a workout's metrics."""
    if not DATABASE_URL or not garmin_metrics:
        return False
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE fitness_workouts SET metrics = COALESCE(metrics, '{}'::jsonb) || %s::jsonb WHERE id = %s",
                (json.dumps(garmin_metrics, ensure_ascii=False), workout_id),
            )
            updated = cur.rowcount > 0
        conn.commit()
        return updated
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
    extras: dict | None = None,
    ai_summary: str = "",
) -> int:
    """Append a new measurement row — history is never overwritten, so every update
    keeps the previous values for trend tracking."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fitness_body_metrics
                  (chat_id, log_date, weight_kg, lbm_kg, smm_kg, bf_pct, source, note, extras, ai_summary)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
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
                    json.dumps(extras or {}, ensure_ascii=False),
                    ai_summary or "",
                ),
            )
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id
    finally:
        conn.close()


def update_body_metric_ai(metric_id: int, ai_summary: str) -> None:
    """Attach the AI trend analysis to an already-inserted measurement."""
    if not DATABASE_URL:
        return
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE fitness_body_metrics SET ai_summary = %s WHERE id = %s",
                (ai_summary or "", metric_id),
            )
        conn.commit()
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
                SELECT id, log_date, weight_kg, lbm_kg, smm_kg, bf_pct, source, note,
                       extras, ai_summary
                FROM fitness_body_metrics
                WHERE chat_id = %s AND log_date >= %s
                ORDER BY log_date ASC, created_at ASC
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
            "extras": r.get("extras") or {},
            "ai_summary": r.get("ai_summary") or "",
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
  "workout_type": "<one of: strength | hiit | cardio | skiing | running | swimming | other>",
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
  },
  "is_estimated_volume": <boolean — true only when fallback heuristic was applied>,
  "user_feedback": "<Hebrew feedback message, or empty string>"
}

Rules:
- title must be in Hebrew.
- workout_type: infer from context (gym exercises → strength; running/cycling → cardio/running; intervals → hiit; ski/snowboard → skiing; pool/swimming/laps → swimming).
- avg_rpe: estimate from text, or 0 if not mentioned.
- exercises: include all mentioned exercises. Omit unknown values (use 0).
- metrics: populate from wearable screenshots or text. Use null for unknown fields.
- If no exercises (pure cardio), exercises may be [].
- Do NOT include body metrics (weight, BF%) in this response.

CRITICAL FALLBACK — missing exercises/sets/reps with a known weight:
If the user provides a workout type AND a primary weight (e.g. "HIIT עם 8 קג", "circuit 10kg") but omits
specific exercises, sets, or reps, do NOT set total volume to 0. Instead:
1. Record the primary weight as a single exercise row with name "Circuit / HIIT", sets=1, reps=100, weight_kg=<primary_weight>.
2. This yields total_volume = primary_weight × 100 (represents ~30-min average circuit).
3. Set is_estimated_volume=true.
4. Set user_feedback to a friendly Hebrew message, e.g.:
   "נרשם! 💡 הנפח חושב כהערכה (לפי ממוצע של 100 חזרות) כיוון שלא צוינו תרגילים וסטים. לחישוב מדויק יותר להבא, כדאי לפרט אותם."
When exercises ARE specified, set is_estimated_volume=false and user_feedback=""."""


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
    parsed = _normalize_workout(raw)
    return _apply_volume_fallback(parsed, text)


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
    return _normalize_workout(raw)  # no text hint for image path


_PARSE_BODY_SCAN_INSTRUCTIONS = """\
You are a body-composition analyst. The image is a printed/computerized body-composition
summary sheet (e.g. InBody 270/570 / Tanita / gym scale report), possibly in Hebrew or
English. Extract EVERY measurement you can read. Respond with ONLY a single JSON object —
no markdown, no code fences.

Schema:
{
  "weight_kg": <number or null>,
  "lbm_kg": <number or null>,          // lean body mass — on InBody sheets this is "Fat Free Mass" (FFM); מסת גוף רזה
  "smm_kg": <number or null>,          // "SMM" / Skeletal Muscle Mass / מסת שריר שלד
  "bf_pct": <number or null>,          // "PBF" / Percent Body Fat / אחוז שומן
  "measured_date": "<YYYY-MM-DD or null — the Test Date printed on the sheet.
                     InBody prints it European style DD.MM.YYYY (e.g. 06.07.2026 = July 6th) — convert carefully>",
  "extras": {                           // include ONLY keys visible on the sheet
    "bmi": <number>,
    "bmr_kcal": <number>,               // "Basal Metabolic Rate"
    "body_fat_kg": <number>,            // "Body Fat Mass"
    "visceral_fat_level": <number>,
    "total_body_water_l": <number>,
    "protein_kg": <number>,
    "minerals_kg": <number>,
    "waist_hip_ratio": <number>,
    "inbody_score": <number>,           // "InBody Score" X/100
    "target_weight_kg": <number>,       // "Target Weight" under Weight Control
    "smi": <number>,                    // Skeletal Muscle Index
    "recommended_kcal": <number>,       // "Recommended calorie intake"
    "segmental_notes": "<short text ONLY if segmental lean analysis shows a marked left/right or limb imbalance; omit when all segments are Normal>"
  }
}
Numbers only (strip units and normal-range parentheses). If a value is not on the sheet,
omit it from extras / use null for the top-level fields. Never invent values."""


async def parse_body_scan_image(image_bytes: bytes, mime_type: str = "image/jpeg", chat_id: str = "") -> dict:
    """Extract all body-composition measurements from a scan/summary-sheet photo."""
    import base64
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import get_gemini_llm

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_uri = f"data:{mime_type or 'image/jpeg'};base64,{b64}"
    llm = get_gemini_llm()
    messages = [
        SystemMessage(content=_PARSE_BODY_SCAN_INSTRUCTIONS),
        HumanMessage(content=[
            {"type": "text", "text": "Extract all body-composition data from this summary sheet."},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]),
    ]
    resp = await llm.ainvoke(messages)
    raw = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
    out = {
        "weight_kg": round(_num(raw.get("weight_kg")), 1) if raw.get("weight_kg") is not None else None,
        "lbm_kg": round(_num(raw.get("lbm_kg")), 1) if raw.get("lbm_kg") is not None else None,
        "smm_kg": round(_num(raw.get("smm_kg")), 1) if raw.get("smm_kg") is not None else None,
        "bf_pct": round(_num(raw.get("bf_pct")), 1) if raw.get("bf_pct") is not None else None,
        "extras": raw.get("extras") if isinstance(raw.get("extras"), dict) else {},
        "measured_date": None,
    }
    md = raw.get("measured_date")
    if md:
        try:
            out["measured_date"] = date.fromisoformat(str(md)[:10])
        except ValueError:
            pass
    if all(out[k] is None for k in ("weight_kg", "lbm_kg", "smm_kg", "bf_pct")) and not out["extras"]:
        raise ValueError("No body-composition values could be read from the image")
    return out


def _body_metric_deltas(new: dict, prev: dict | None) -> dict:
    """Deterministic per-metric change vs the previous measurement (never LLM-guessed).
    Positive delta = value went up. 'good' reflects the user's goal:
    LBM/SMM up = good, BF% down = good, weight judged by composition context."""
    deltas: dict = {}
    if not prev:
        return deltas
    for key, good_when in (("weight_kg", None), ("lbm_kg", "up"), ("smm_kg", "up"), ("bf_pct", "down")):
        nv, pv = new.get(key), prev.get(key)
        if nv is None or pv is None:
            continue
        d = round(float(nv) - float(pv), 1)
        entry = {"from": float(pv), "to": float(nv), "delta": d}
        if good_when and d != 0:
            entry["good"] = (d > 0) if good_when == "up" else (d < 0)
        deltas[key] = entry
    return deltas


_EVALUATE_BODY_INSTRUCTIONS = """\
You are a body-composition coach AI. Write a short Hebrew analysis of the user's new
body measurement versus the previous one. The numeric deltas are ALREADY COMPUTED —
do NOT recompute or contradict them. Respond with ONLY a single JSON object:

{
  "ai_summary": "<Hebrew, 2-4 sentences: what improved, what regressed, overall opinion
                  relative to the goal (build lean mass, reduce BF%), and ONE concrete
                  actionable recommendation. If this is the first measurement, welcome the
                  baseline and say what to watch going forward.>"
}

MANDATORY: relate to LBM/SMM direction (muscle) and BF% direction (fat) explicitly when
present; include the Gilbert's-Syndrome hydration reminder only if total body water or
hydration appears low; keep an encouraging, professional tone."""


async def evaluate_body_metrics(new_metrics: dict, chat_id: str = "") -> dict:
    """AI opinion on a new measurement vs the previous one. Returns {ai_summary, deltas}."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import get_gemini_llm
    from app.fitness_profile import render_profile_block

    history = body_metrics_history(chat_id, days=365)
    # The just-inserted row is last; the previous distinct measurement precedes it.
    prev = history[-2] if len(history) >= 2 else None
    deltas = _body_metric_deltas(new_metrics, prev)

    context = {
        "new_measurement": {k: new_metrics.get(k) for k in ("weight_kg", "lbm_kg", "smm_kg", "bf_pct")},
        "new_extras": new_metrics.get("extras") or {},
        "previous_measurement": (
            {k: prev.get(k) for k in ("log_date", "weight_kg", "lbm_kg", "smm_kg", "bf_pct")} if prev else None
        ),
        "computed_deltas": deltas,
        "measurements_in_last_year": len(history),
    }

    ai_summary = ""
    try:
        llm = get_gemini_llm()
        profile = render_profile_block(None)
        messages = [
            SystemMessage(content=f"{profile}\n\n{_EVALUATE_BODY_INSTRUCTIONS}"),
            HumanMessage(content=f"New body measurement (deltas already computed):\n{json.dumps(context, ensure_ascii=False, indent=2, default=str)}"),
        ]
        resp = await llm.ainvoke(messages)
        raw = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
        ai_summary = str(raw.get("ai_summary") or "")
    except Exception as exc:
        logger.warning("evaluate_body_metrics narrative failed: %s", exc)
        # Deterministic fallback so the user always gets *something* useful.
        if deltas:
            parts = []
            labels = {"weight_kg": "משקל", "lbm_kg": "מסה רזה", "smm_kg": "מסת שריר", "bf_pct": "אחוז שומן"}
            for k, d in deltas.items():
                arrow = "עלה" if d["delta"] > 0 else ("ירד" if d["delta"] < 0 else "ללא שינוי")
                parts.append(f"{labels[k]} {arrow} ({d['from']}→{d['to']})")
            ai_summary = "עדכון נתוני גוף: " + ", ".join(parts) + "."
        else:
            ai_summary = "מדידת בסיס נשמרה — מהמדידה הבאה נעקוב אחרי מגמות שיפור."

    return {"ai_summary": ai_summary, "deltas": deltas}


def _apply_volume_fallback(parsed: dict, text: str = "") -> dict:
    """Python-level fallback: if LLM returned 0 volume but a weight is detectable in the text,
    synthesise a single Circuit/HIIT exercise row so the volume is never silently 0."""
    if _total_volume(parsed["exercises"]) > 0 or parsed.get("is_estimated_volume"):
        return parsed  # already handled
    # If exercises have duration_sec > 0 but no weight, don't overwrite them with a synthetic row
    if any(_num(ex.get("duration_sec")) > 0 for ex in parsed.get("exercises") or []):
        return parsed

    # Detect a primary weight anywhere in the original text (kg / קג / קילו)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|קג|קילו|kilo)", text, re.IGNORECASE)
    if not m:
        return parsed  # no weight hint — can't estimate

    w = float(m.group(1))
    parsed["exercises"] = [{
        "name": "Circuit / HIIT",
        "sets": 1,
        "reps": 100,
        "weight_kg": w,
        "rpe": round(parsed.get("avg_rpe") or 0, 1),
        "duration_sec": 0,
        "notes": "estimated",
    }]
    parsed["is_estimated_volume"] = True
    parsed["user_feedback"] = (
        f"נרשם! 💡 הנפח חושב כהערכה ({w:.0f} קג × 100 חזרות = {w * 100:.0f} קג) "
        "כיוון שלא צוינו תרגילים וסטים. לחישוב מדויק יותר להבא, כדאי לפרט אותם."
    )
    return parsed


def _normalize_workout(parsed: dict) -> dict:
    valid_types = {"strength", "hiit", "cardio", "skiing", "running", "swimming", "other"}
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
        "is_estimated_volume": bool(parsed.get("is_estimated_volume", False)),
        "user_feedback": str(parsed.get("user_feedback") or "").strip(),
    }


def _compute_progression_targets(workout: dict) -> tuple[list, str]:
    """Deterministically compute next-session targets from per-exercise RPE.

    Progressive-overload rules (no LLM — pure arithmetic):
      RPE ≤ 6      → weight +5%
      RPE 7-8      → weight +2.5%
      RPE = 9      → hold weight, focus on form
      RPE ≥ 10     → deload 10%
      bodyweight   → +1-2 reps
    Returns (targets, focus) where focus ∈ progressive_overload|maintain|deload|recovery.
    """
    targets: list = []
    deload_any = False
    increase_any = False
    hold_any = False

    for ex in workout.get("exercises") or []:
        name = str(ex.get("name") or "").strip()
        if not name:
            continue
        rpe = _num(ex.get("rpe")) or _num(workout.get("avg_rpe"))
        weight = _num(ex.get("weight_kg"))
        reps = int(_num(ex.get("reps")))
        duration_sec = int(_num(ex.get("duration_sec")))

        # Timed exercise (duration_sec > 0, reps = 0) — progress by time or weight
        if reps == 0 and duration_sec > 0:
            if weight <= 0:
                new_dur = duration_sec + 5
                targets.append({
                    "name": name,
                    "target_weight_kg": 0.0,
                    "target_reps": 0,
                    "target_duration_sec": new_dur,
                    "rationale": f"תרגיל מתוזמן — הוסף 5 שניות ({duration_sec}s → {new_dur}s).",
                })
            else:
                if rpe and rpe >= 9:
                    targets.append({"name": name, "target_weight_kg": weight, "target_reps": 0,
                                    "target_duration_sec": duration_sec,
                                    "rationale": "RPE גבוה — שמור משקל וזמן."})
                    hold_any = True
                else:
                    new_dur = duration_sec + 5
                    targets.append({"name": name, "target_weight_kg": weight, "target_reps": 0,
                                    "target_duration_sec": new_dur,
                                    "rationale": f"הוסף 5 שניות ({duration_sec}s → {new_dur}s), שמור משקל {weight}kg."})
                    increase_any = True
            continue

        if weight <= 0:
            # Bodyweight / cardio — progress by reps
            target_reps = reps + 2 if reps > 0 else 0
            targets.append({
                "name": name,
                "target_weight_kg": 0.0,
                "target_reps": target_reps,
                "rationale": "תרגיל משקל גוף — הוסף 1-2 חזרות בכל סט." if target_reps else "המשך באותו נפח.",
            })
            if target_reps > reps:
                increase_any = True
            continue

        if rpe and rpe >= 10:
            new_w = round(weight * 0.90, 1)
            targets.append({"name": name, "target_weight_kg": new_w, "target_reps": reps,
                            "rationale": f"RPE 10 — הורדת משקל ל-{new_w} קג להתאוששות."})
            deload_any = True
        elif rpe and rpe >= 9:
            targets.append({"name": name, "target_weight_kg": weight, "target_reps": reps,
                            "rationale": "RPE 9 — שמור משקל, התמקד בטכניקה."})
            hold_any = True
        elif rpe and rpe >= 7:
            new_w = round(weight * 1.025, 1)
            targets.append({"name": name, "target_weight_kg": new_w, "target_reps": reps,
                            "rationale": f"RPE {rpe:.0f} — העלה ל-{new_w} קג (+2.5%)."})
            increase_any = True
        else:
            # RPE ≤ 6 (or unknown but with weight) → +5%
            new_w = round(weight * 1.05, 1)
            targets.append({"name": name, "target_weight_kg": new_w, "target_reps": reps,
                            "rationale": f"RPE נמוך — העלה ל-{new_w} קג (+5%)."})
            increase_any = True

    avg_rpe = _num(workout.get("avg_rpe"))
    if avg_rpe >= 9 or deload_any:
        focus = "recovery" if avg_rpe >= 9 else "deload"
    elif increase_any:
        focus = "progressive_overload"
    elif hold_any:
        focus = "maintain"
    else:
        focus = "maintain"
    return targets, focus


_EVALUATE_INSTRUCTIONS = """\
You are a personal trainer AI. Write a short Hebrew evaluation of the completed workout.
The progressive-overload targets are ALREADY COMPUTED for you — do NOT recompute weights or reps.
Respond with ONLY a single JSON object — no markdown, no code fences.

Schema:
{
  "ai_summary": "<1-2 sentence Hebrew summary of the workout and key observations>",
  "next_summary": "<1-2 sentence Hebrew recommendation for the next session>"
}

If "exercises" is empty but "session_metrics" has data (a cardio/cycling/running activity,
often auto-imported from a Garmin watch), base ai_summary on those metrics instead —
comment on heart rate (avg/max), pace or speed, distance, and calories; do not say there
is nothing to evaluate.

MANDATORY (always include in ai_summary):
- A hydration reminder (Gilbert's Syndrome).
- If avg_rpe ≥ 9, mention recovery/rest is the priority.
- If neck/shoulder exercises are present, add a neutral-neck form cue.
- If this is a strength session, encourage scapular stabilizer work (face pulls, band pull-aparts, prone Y/T/W)."""


async def evaluate_workout(workout: dict, chat_id: str = "") -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import get_gemini_llm
    from app.fitness_profile import render_profile_block

    # Deterministic math — never delegated to the LLM.
    targets, focus = _compute_progression_targets(workout)

    metrics = latest_body_metrics(chat_id)
    profile = render_profile_block(metrics)

    # Hand the computed targets to the LLM so its prose matches the numbers.
    context = {
        "title": workout.get("title"),
        "workout_type": workout.get("workout_type"),
        "avg_rpe": workout.get("avg_rpe"),
        "exercises": workout.get("exercises"),
        "session_metrics": workout.get("metrics") or {},
        "computed_targets": targets,
        "focus": focus,
    }

    ai_summary = ""
    next_summary = ""
    try:
        llm = get_gemini_llm()
        messages = [
            SystemMessage(content=f"{profile}\n\n{_EVALUATE_INSTRUCTIONS}"),
            HumanMessage(content=f"Completed workout (targets already computed):\n{json.dumps(context, ensure_ascii=False, indent=2)}"),
        ]
        resp = await llm.ainvoke(messages)
        raw = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
        ai_summary = str(raw.get("ai_summary") or "")
        next_summary = str(raw.get("next_summary") or "")
    except Exception as exc:
        logger.warning("evaluate_workout narrative failed, using targets only: %s", exc)

    return {
        "ai_summary": ai_summary,
        "ai_next_rec": {
            "summary": next_summary,
            "focus": focus,
            "targets": targets,
        },
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
For swimming workouts: exercises should be swim sets (e.g. "4×50m freestyle", "200m pull buoy"), weight_kg=0, duration_sec is the set time estimate. No scapular constraint applies in water; focus on stroke technique, breathing, distance targets. Still include hydration reminder.

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
The recent-history JSON below includes ALL logged sessions regardless of source — manual
logs, photo logs, and activities auto-imported from a Garmin watch (source="garmin", which
may include cardio/cycling/running sessions with HR/distance/pace in "metrics" but no
"exercises"). Treat these as real training load: factor recent cardio volume and intensity
(duration, avg/max HR) into today's fatigue/recovery consideration and workout_type choice
the same way you would a logged strength session.
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
    from app.fitness_profile import render_profile_block
    from app.config import FITNESS_WEEKLY_SESSION_TARGET

    # Reuse the deterministic (and per-day cached) recommendation rather than
    # dumping raw 7-day JSON and asking the LLM to count sessions itself.
    rec = await generate_daily_recommendation(chat_id)
    metrics = latest_body_metrics(chat_id)
    profile = render_profile_block(metrics)

    last_str = (
        "מעולם לא תועד אימון" if rec["days_since_last"] < 0
        else "אומן היום" if rec["days_since_last"] == 0
        else "אתמול" if rec["days_since_last"] == 1
        else f"לפני {rec['days_since_last']} ימים"
    )
    facts = {
        "weekly_done": rec["weekly_done"],
        "weekly_target": FITNESS_WEEKLY_SESSION_TARGET,
        "days_since_last_workout": last_str,
        "last_avg_rpe": rec["last_avg_rpe"],
        "today_readiness": rec["readiness_he"],
        "today_workout_type": rec["workout_type"],
        "today_title": rec["title"],
        "today_duration_min": rec["duration_min"],
        "today_key_exercises": rec["key_exercises"],
    }
    garmin = rec.get("garmin")
    if garmin:
        facts["garmin_watch_today"] = {
            "sleep_score": garmin.get("sleep_score"),
            "sleep_hours": round(garmin["sleep_min"] / 60, 1) if garmin.get("sleep_min") else None,
            "body_battery_high": garmin.get("bb_high"),
            "resting_hr": garmin.get("resting_hr"),
        }
        if rec.get("garmin_flags"):
            facts["garmin_readiness_flags"] = rec["garmin_flags"]

    instructions = """\
You are a personal trainer AI giving a daily morning fitness brief in Hebrew.
All stats below are ALREADY COMPUTED — do NOT change the numbers or the readiness decision.
Write a concise WhatsApp-friendly brief (max 150 words) that covers, in this order:
1. Weekly compliance (sessions done / target).
2. Recovery status (days since last session + last avg RPE).
3. Today's recommendation (readiness, type, duration, key exercises).
4. Hydration reminder (Gilbert's Syndrome — min 2.5L today).
5. Neck/scapula reminder if today is a strength session.
Format: *bold* for headers, • for bullets, no # headers."""

    try:
        llm = get_gemini_llm()
        messages = [
            SystemMessage(content=f"{profile}\n\n{instructions}"),
            HumanMessage(content=f"Computed facts:\n{json.dumps(facts, ensure_ascii=False, indent=2)}"),
        ]
        resp = await llm.ainvoke(messages)
        text = str(resp.content if hasattr(resp, "content") else str(resp)).strip()
        if text:
            return text
    except Exception as exc:
        logger.warning("morning brief narrative failed, using template: %s", exc)

    # Deterministic fallback — never leaves the user without a brief.
    lines = [
        f"*כושר בוקר* 🏋️",
        f"• השבוע: {rec['weekly_done']}/{FITNESS_WEEKLY_SESSION_TARGET} אימונים",
        f"• אימון אחרון: {last_str}" + (f" (RPE {rec['last_avg_rpe']})" if rec['last_avg_rpe'] else ""),
        f"• היום: {rec['readiness_he']} — {rec['title']} ({rec['duration_min']} דק')",
        "• הידרציה: לפחות 2.5 ליטר מים היום 💧",
    ]
    return "\n".join(lines)


# Per-process cache keyed by (chat_id, YYYY-MM-DD) so we pay the LLM cost once per day.
_daily_rec_cache: dict = {}


async def generate_daily_recommendation(chat_id: str) -> dict:
    """Return a structured daily workout recommendation, cached per calendar day.

    The returned dict always has:
      readiness        : "ready" | "light" | "rest"
      readiness_he     : Hebrew label
      workout_type     : strength | cardio | swimming | rest
      title            : Hebrew workout title
      duration_min     : int
      rationale        : Hebrew 1-2 sentence rationale
      weekly_done      : int sessions this week
      weekly_target    : int
      key_exercises    : [{name, sets, reps, weight_kg}]  (empty for rest)
      days_since_last  : int (-1 if never)
      last_avg_rpe     : float | null
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import get_gemini_llm
    from app.fitness_profile import render_profile_block
    from app.config import FITNESS_WEEKLY_SESSION_TARGET
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(USER_TIMEZONE)
    today_str = datetime.now(tz=tz).strftime("%Y-%m-%d")
    cache_key = (_fitness_key(chat_id), today_str)
    if cache_key in _daily_rec_cache:
        cached = _daily_rec_cache[cache_key]
        # Garmin may sync after the rec was generated — refresh the wellness snapshot
        # on cache hits (cheap DB read; the readiness decision itself stays cached).
        if not cached.get("garmin") and _garmin_allowed():
            try:
                from app.garmin.sync import get_wellness as _garmin_get_wellness
                cached["garmin"] = _garmin_get_wellness(_user_today())
            except Exception:
                pass
        return cached

    fkey = _fitness_key(chat_id)
    recent = history(fkey, days=14)
    metrics = latest_body_metrics(fkey)
    profile = render_profile_block(metrics)

    # ── Deterministic stats (never guessed by the LLM) ──
    today_date = _user_today()
    days_since_last = -1
    last_avg_rpe = None
    weekly_done = 0
    last_next_rec: dict = {}
    trained_dates: set = set()

    for day in recent:
        day_date = date.fromisoformat(day["date"]) if isinstance(day.get("date"), str) else None
        if day_date and day.get("session_count", 0) > 0:
            trained_dates.add(day_date)
            days_ago = (today_date - day_date).days
            if days_since_last == -1:
                days_since_last = days_ago
                last_avg_rpe = day.get("avg_rpe")
                sessions = day.get("sessions") or []
                if sessions:
                    last_next_rec = sessions[-1].get("ai_next_rec") or {}
            if days_ago < 7:
                weekly_done += day.get("session_count", 0)

    # Consecutive training days ending today (for the 3-in-a-row rest rule)
    consecutive = 0
    cursor = today_date
    while cursor in trained_dates:
        consecutive += 1
        cursor = cursor - timedelta(days=1)

    # ── Deterministic readiness decision ──
    rpe = last_avg_rpe if last_avg_rpe is not None else 0
    if days_since_last == 0 and (rpe >= 8 or consecutive >= 3):
        readiness, readiness_he, workout_type = "rest", "מנוחה", "rest"
    elif days_since_last == 0 or rpe >= 8:
        readiness, readiness_he, workout_type = "light", "אימון קל", "cardio"
    else:
        readiness, readiness_he, workout_type = "ready", "מוכן לאימון", "strength"

    # ── Garmin wellness overlay (Venu 2: sleep score / Body Battery) ──
    # Physiological signals can only DOWNGRADE readiness — the RPE/frequency
    # rules above stay authoritative for upgrades.
    garmin_wellness = None
    garmin_flags: list[str] = []
    if _garmin_allowed():
        try:
            from app.garmin.sync import get_wellness as _garmin_get_wellness
            garmin_wellness = _garmin_get_wellness(today_date)
        except Exception:
            garmin_wellness = None
    if garmin_wellness:
        sleep_score = garmin_wellness.get("sleep_score")
        bb_high = garmin_wellness.get("bb_high")
        if readiness == "ready" and (
            (sleep_score is not None and sleep_score < 55)
            or (bb_high is not None and bb_high < 35)
        ):
            readiness, readiness_he, workout_type = "light", "אימון קל", "cardio"
            if sleep_score is not None and sleep_score < 55:
                garmin_flags.append("שינה ירודה")
            if bb_high is not None and bb_high < 35:
                garmin_flags.append("סוללת גוף נמוכה")
        elif readiness == "light" and sleep_score is not None and sleep_score < 40:
            readiness, readiness_he, workout_type = "rest", "מנוחה", "rest"
            garmin_flags.append("שינה ירודה מאוד")

    # ── Deterministic key_exercises from last session's computed targets ──
    key_exercises: list = []
    if readiness != "rest":
        for t in (last_next_rec.get("targets") or [])[:3]:
            if t.get("name"):
                key_exercises.append({
                    "name": str(t["name"]),
                    "sets": 3,
                    "reps": int(_num(t.get("target_reps") or 10)),
                    "weight_kg": round(_num(t.get("target_weight_kg") or 0), 1),
                })

    # ── LLM writes ONLY the narrative (title + rationale), and fills exercises
    #    only when we have no prior targets to carry forward. ──
    need_exercises = readiness != "rest" and not key_exercises
    garmin_facts = ""
    if garmin_wellness:
        gparts = []
        if garmin_wellness.get("sleep_score") is not None:
            gparts.append(f"sleep score {garmin_wellness['sleep_score']}/100")
        if garmin_wellness.get("sleep_min"):
            gparts.append(f"slept {round(garmin_wellness['sleep_min'] / 60, 1)}h")
        if garmin_wellness.get("bb_high") is not None:
            gparts.append(f"Body Battery high {garmin_wellness['bb_high']}")
        if garmin_wellness.get("resting_hr") is not None:
            gparts.append(f"resting HR {garmin_wellness['resting_hr']}")
        if gparts:
            garmin_facts = "\nGarmin watch (Venu 2) today: " + ", ".join(gparts) + "."
            if garmin_flags:
                garmin_facts += f" Readiness was downgraded because of: {', '.join(garmin_flags)} — mention this in the rationale."
    instructions = f"""\
You are a personal trainer AI writing today's recommendation in Hebrew.
The readiness decision and weekly stats are ALREADY DECIDED — do NOT change them.
Today's readiness: {readiness} ({readiness_he}). Workout type: {workout_type}.
This week: {weekly_done}/{FITNESS_WEEKLY_SESSION_TARGET} sessions. Days since last: {days_since_last if days_since_last >= 0 else 'never'}. Last avg RPE: {rpe or 'unknown'}.{garmin_facts}

Return ONLY a JSON object (no markdown fences):
{{
  "title": "<Hebrew workout title matching the readiness, e.g. אימון כוח עליון / מנוחה אקטיבית>",
  "duration_min": <int, 0 if rest>,
  "rationale": "<Hebrew 1-2 sentences — why this fits today, mention recovery + hydration>"{(''',
  "key_exercises": [{"name": "<name>", "sets": <int>, "reps": <int>, "weight_kg": <number>}]''') if need_exercises else ''}
}}
{('''Rules for key_exercises (3 items): NEVER heavy back squat/deadlift/military press; ALWAYS include one scapular stabilizer (face pulls / band pull-aparts / prone Y-T-W).''') if need_exercises else ''}"""

    title = "מנוחה" if readiness == "rest" else "אימון מוצע"
    duration_min = 0 if readiness == "rest" else 45
    rationale = ""
    try:
        llm = get_gemini_llm()
        messages = [
            SystemMessage(content=f"{profile}\n\n{instructions}"),
            HumanMessage(content="Generate today's recommendation."),
        ]
        resp = await llm.ainvoke(messages)
        raw = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
        title = str(raw.get("title") or title)
        duration_min = int(_num(raw.get("duration_min"))) if raw.get("duration_min") is not None else duration_min
        rationale = str(raw.get("rationale") or "")
        if need_exercises:
            for e in (raw.get("key_exercises") or [])[:3]:
                if e.get("name"):
                    key_exercises.append({
                        "name": str(e["name"]),
                        "sets": int(_num(e.get("sets") or 3)),
                        "reps": int(_num(e.get("reps") or 10)),
                        "weight_kg": round(_num(e.get("weight_kg") or 0), 1),
                    })
    except Exception as exc:
        logger.warning("daily-rec narrative failed, using deterministic core: %s", exc)

    result: dict = {
        "readiness": readiness,
        "readiness_he": readiness_he,
        "workout_type": workout_type,
        "title": title,
        "duration_min": duration_min,
        "rationale": rationale,
        "weekly_done": weekly_done,
        "weekly_target": FITNESS_WEEKLY_SESSION_TARGET,
        "key_exercises": key_exercises,
        "days_since_last": days_since_last,
        "last_avg_rpe": last_avg_rpe,
        "garmin": garmin_wellness,
        "garmin_flags": garmin_flags,
        "cached_at": today_str,
    }
    _daily_rec_cache[cache_key] = result
    return result

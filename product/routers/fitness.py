"""Tenant-scoped fitness API for the product web app.

Thin wrapper over the engine's fitness data layer (app.fitness). Auth is the
product session (require_module → require_session → sets current_tenant_id +
CSRF + rate limit + 'fitness' toggle). Data functions derive their scope from
current_tenant_id, so the chat_id argument is vestigial — we always pass "".

Garmin: a tenant only sees a wellness overlay if they connected their own
account (product/routers/garmin.py) — garmin_tokens and garmin_wellness are
tenant-keyed (P2/P4), and fitness._garmin_allowed() checks the calling
tenant's own connection state, never the owner's.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app import fitness
from product.deps import require_module
from product.tenancy.models import Tenant

logger = logging.getLogger("product.fitness")

router = APIRouter(prefix="/api/fitness")

_MAX_IMAGE_BYTES = 12 * 1024 * 1024  # 12 MB
_gate = require_module("fitness")


class TextLog(BaseModel):
    text: str


class StructuredWorkout(BaseModel):
    title: str = ""
    workout_type: str = "strength"
    duration_min: float = 0
    avg_rpe: float = 0
    exercises: list = []
    metrics: dict = {}
    source: str = "text"


class SuggestRequest(BaseModel):
    minutes: int = 45
    workout_type: str = "strength"


class EvaluateRequest(BaseModel):
    workout_id: int


class BodyMetricsRequest(BaseModel):
    weight_kg: Optional[float] = None
    lbm_kg: Optional[float] = None
    smm_kg: Optional[float] = None
    bf_pct: Optional[float] = None
    note: str = ""


async def _log_and_evaluate(parsed: dict, source: str) -> dict:
    workout_id = fitness.insert_workout(
        chat_id="", workout_type=parsed["workout_type"], title=parsed["title"],
        duration_min=parsed["duration_min"], avg_rpe=parsed["avg_rpe"],
        exercises=parsed["exercises"], metrics=parsed["metrics"], source=source,
    )
    try:
        evaluation = await fitness.evaluate_workout(parsed, "")
        fitness.update_workout_ai(workout_id, evaluation["ai_summary"], evaluation["ai_next_rec"])
    except Exception:
        logger.exception("fitness evaluate failed")
        evaluation = {"ai_summary": "", "ai_next_rec": {}}
    return {"id": workout_id, "entry": parsed, "evaluation": evaluation, "today": fitness.list_today("")}


@router.get("/today")
async def today(_: Tenant = Depends(_gate)):
    return fitness.list_today("")


@router.get("/history")
async def history(days: int = Query(30, ge=1, le=180), _: Tenant = Depends(_gate)):
    return {"days": fitness.history("", days)}


@router.post("/log-text")
async def log_text(body: TextLog, _: Tenant = Depends(_gate)):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty_workout_text")
    try:
        parsed = await fitness.parse_workout_text(text, "")
    except Exception as exc:
        logger.exception("fitness log-text parse failed")
        raise HTTPException(status_code=502, detail=f"analyze_failed: {exc}")
    return await _log_and_evaluate(parsed, "text")


@router.post("/log-image")
async def log_image(file: UploadFile, _: Tenant = Depends(_gate)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty_image")
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="image_too_large")
    try:
        parsed = await fitness.parse_workout_image(data, file.content_type or "image/jpeg", "")
    except Exception as exc:
        logger.exception("fitness log-image parse failed")
        raise HTTPException(status_code=502, detail=f"analyze_failed: {exc}")
    return await _log_and_evaluate(parsed, "image")


@router.post("/log-structured")
async def log_structured(body: StructuredWorkout, _: Tenant = Depends(_gate)):
    workout_id = fitness.insert_workout(
        chat_id="", workout_type=body.workout_type, title=body.title,
        duration_min=body.duration_min, avg_rpe=body.avg_rpe,
        exercises=body.exercises, metrics=body.metrics, source=body.source,
    )
    workout = fitness.get_workout(workout_id, "")
    try:
        evaluation = await fitness.evaluate_workout(workout or {}, "")
        fitness.update_workout_ai(workout_id, evaluation["ai_summary"], evaluation["ai_next_rec"])
    except Exception:
        logger.exception("fitness evaluate failed")
        evaluation = {"ai_summary": "", "ai_next_rec": {}}
    return {"id": workout_id, "evaluation": evaluation, "today": fitness.list_today("")}


@router.post("/suggest")
async def suggest(body: SuggestRequest, _: Tenant = Depends(_gate)):
    try:
        return await fitness.suggest_workout("", minutes=body.minutes, workout_type=body.workout_type)
    except Exception as exc:
        logger.exception("fitness suggest failed")
        raise HTTPException(status_code=502, detail=f"suggest_failed: {exc}")


@router.post("/evaluate")
async def evaluate(body: EvaluateRequest, _: Tenant = Depends(_gate)):
    workout = fitness.get_workout(body.workout_id, "")
    if not workout:
        raise HTTPException(status_code=404, detail="workout_not_found")
    try:
        evaluation = await fitness.evaluate_workout(workout, "")
        fitness.update_workout_ai(body.workout_id, evaluation["ai_summary"], evaluation["ai_next_rec"])
    except Exception as exc:
        logger.exception("fitness evaluate failed")
        raise HTTPException(status_code=502, detail=f"evaluate_failed: {exc}")
    return evaluation


@router.get("/morning-brief")
async def morning_brief(_: Tenant = Depends(_gate)):
    try:
        return {"brief": await fitness.generate_morning_brief("")}
    except Exception as exc:
        logger.exception("fitness morning brief failed")
        raise HTTPException(status_code=502, detail=f"brief_failed: {exc}")


@router.get("/daily-rec")
async def daily_recommendation(_: Tenant = Depends(_gate)):
    try:
        return await fitness.generate_daily_recommendation("")
    except Exception as exc:
        logger.exception("fitness daily-rec failed")
        raise HTTPException(status_code=502, detail=f"daily_rec_failed: {exc}")


@router.get("/body-metrics")
async def get_body_metrics(days: int = Query(180, ge=1, le=365), _: Tenant = Depends(_gate)):
    return {
        "latest": fitness.latest_body_metrics(""),
        "history": fitness.body_metrics_history("", days),
    }


@router.post("/body-metrics")
async def post_body_metrics(body: BodyMetricsRequest, _: Tenant = Depends(_gate)):
    if all(v is None for v in [body.weight_kg, body.lbm_kg, body.smm_kg, body.bf_pct]):
        raise HTTPException(status_code=400, detail="metric_required")
    metric_id = fitness.insert_body_metric(
        chat_id="", weight_kg=body.weight_kg, lbm_kg=body.lbm_kg,
        smm_kg=body.smm_kg, bf_pct=body.bf_pct, note=body.note, source="manual",
    )
    analysis = {"ai_summary": "", "deltas": {}}
    try:
        analysis = await fitness.evaluate_body_metrics(
            {"weight_kg": body.weight_kg, "lbm_kg": body.lbm_kg,
             "smm_kg": body.smm_kg, "bf_pct": body.bf_pct, "extras": {}}, "",
        )
        fitness.update_body_metric_ai(metric_id, analysis["ai_summary"])
    except Exception:
        logger.exception("body metrics AI evaluation failed")
    return {"id": metric_id, "analysis": analysis, "latest": fitness.latest_body_metrics("")}


@router.post("/body-metrics-image")
async def post_body_metrics_image(file: UploadFile, _: Tenant = Depends(_gate)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty_image")
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="image_too_large")
    try:
        parsed = await fitness.parse_body_scan_image(data, file.content_type or "image/jpeg", "")
    except Exception as exc:
        logger.exception("body scan parse failed")
        raise HTTPException(status_code=502, detail=f"scan_failed: {exc}")
    metric_id = fitness.insert_body_metric(
        chat_id="", weight_kg=parsed["weight_kg"], lbm_kg=parsed["lbm_kg"],
        smm_kg=parsed["smm_kg"], bf_pct=parsed["bf_pct"], source="scan",
        note="imported from body-scan image", log_date=parsed.get("measured_date"),
        extras=parsed.get("extras") or {},
    )
    analysis = {"ai_summary": "", "deltas": {}}
    try:
        analysis = await fitness.evaluate_body_metrics(parsed, "")
        fitness.update_body_metric_ai(metric_id, analysis["ai_summary"])
    except Exception:
        logger.exception("body scan AI evaluation failed")
    md = parsed.get("measured_date")
    return {
        "id": metric_id,
        "parsed": {**parsed, "measured_date": md.isoformat() if md else None},
        "analysis": analysis,
        "latest": fitness.latest_body_metrics(""),
        "history": fitness.body_metrics_history("", days=365),
    }


@router.get("/progression")
async def get_progression(
    exercise: str = Query(...),
    days: int = Query(180, ge=1, le=365),
    _: Tenant = Depends(_gate),
):
    return {"progression": fitness.exercise_progression("", exercise, days)}


@router.delete("/body-metrics/{metric_id}")
async def delete_metric(metric_id: int, _: Tenant = Depends(_gate)):
    if not fitness.delete_body_metric(metric_id, ""):
        raise HTTPException(status_code=404, detail="metric_not_found")
    return {"ok": True}


@router.delete("/{workout_id}")
async def delete_entry(workout_id: int, _: Tenant = Depends(_gate)):
    if not fitness.delete_workout(workout_id, ""):
        raise HTTPException(status_code=404, detail="workout_not_found")
    return {"ok": True}


# ── Exercise GIF proxy (static asset lookup, no tenant scope needed) ────────

_GIF_CACHE: dict[str, str | None] = {}
_EQUIP_NOISE = {"machine", "barbell", "dumbbell", "cable", "smith", "kettlebell",
                "band", "bodyweight", "lever", "weighted", "assisted"}


def _norm_exercise_term(name: str) -> str:
    n = re.sub(r"[^a-z0-9\s-]", " ", name.lower())
    words = [w for w in n.split() if w]
    core = [w for w in words if w not in _EQUIP_NOISE]
    return " ".join(core if core else words).strip()


def _gif_of(item: dict) -> str | None:
    for k in ("gifUrl", "imageUrl", "image", "gif"):
        v = item.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    eid = item.get("exerciseId") or item.get("id")
    if eid:
        return f"https://static.exercisedb.dev/media/{eid}.gif"
    return None


def _pick_best(items: list[dict], term: str) -> dict | None:
    if not items:
        return None
    t = term.lower()
    for it in items:
        if str(it.get("name", "")).lower() == t:
            return it
    for it in items:
        if str(it.get("name", "")).lower().startswith(t):
            return it
    for it in items:
        if t in str(it.get("name", "")).lower():
            return it
    return items[0]


@router.get("/exercise-gif")
async def exercise_gif(name: str = Query(""), _: Tenant = Depends(_gate)):
    """Server-side proxy: fetch a demonstration GIF for an exercise from the
    free open-source ExerciseDB V1 API (avoids browser CORS). Cached in-process —
    a pure static-asset lookup, no tenant data involved."""
    term = _norm_exercise_term(name or "")
    if not term:
        return {"gifUrl": None}
    if term in _GIF_CACHE:
        return {"gifUrl": _GIF_CACHE[term]}

    q = urllib.parse.quote(term)
    urls_to_try = [
        f"https://oss.exercisedb.dev/api/v1/exercises/search?q={q}&limit=5",
        f"https://oss.exercisedb.dev/api/v1/exercises/search?search={q}&limit=5",
        f"https://exercisedb.dev/api/v1/exercises/search?q={q}&limit=5",
    ]

    def _fetch(url: str):
        req = urllib.request.Request(
            url, headers={"User-Agent": "danidin-fitness/1.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read().decode())

    loop = asyncio.get_event_loop()
    for url in urls_to_try:
        try:
            data = await loop.run_in_executor(None, _fetch, url)
            items = (
                data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), list)
                else data if isinstance(data, list) else []
            )
            best = _pick_best([i for i in items if isinstance(i, dict)], term)
            gif = _gif_of(best) if best else None
            if gif:
                _GIF_CACHE[term] = gif
                return {"gifUrl": gif}
        except Exception:
            logger.debug("exercise-gif lookup failed for %s via %s", term, url, exc_info=True)
            continue

    _GIF_CACHE[term] = None
    return {"gifUrl": None}

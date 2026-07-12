"""Tenant-scoped fitness API for the product web app.

Thin wrapper over the engine's fitness data layer (app.fitness). Auth is the
product session (require_module → require_session → sets current_tenant_id +
CSRF + rate limit + 'fitness' toggle). Data functions derive their scope from
current_tenant_id, so the chat_id argument is vestigial — we always pass "".

Garmin stays owner-only (single global account); the data layer already
short-circuits its wellness overlay for non-owner scopes (_garmin_allowed).
"""
from __future__ import annotations

import logging
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

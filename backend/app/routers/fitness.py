"""Fitness API — log workouts (image/text/structured), suggest, evaluate, body metrics, history."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from typing import Optional

from app.config import TEST_TOKEN
from app import fitness

logger = logging.getLogger("pa.fitness")
router = APIRouter()
_bearer = HTTPBearer(auto_error=False)

_MAX_IMAGE_BYTES = 12 * 1024 * 1024  # 12 MB


def _require_token(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if not TEST_TOKEN or not creds or creds.credentials != TEST_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return creds.credentials


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


@router.get("/api/fitness/today")
async def today(chat_id: str = Query(...), _: str = Depends(_require_token)):
    return fitness.list_today(chat_id)


@router.get("/api/fitness/history")
async def get_history(
    chat_id: str = Query(...),
    days: int = Query(30, ge=1, le=180),
    _: str = Depends(_require_token),
):
    return {"days": fitness.history(chat_id, days)}


@router.post("/api/fitness/log-text")
async def log_text(
    body: TextLog,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty workout text")
    try:
        parsed = await fitness.parse_workout_text(text, chat_id)
    except Exception as exc:
        logger.exception("fitness log-text parse failed")
        raise HTTPException(status_code=502, detail=f"Could not analyze workout: {exc}")

    workout_id = fitness.insert_workout(
        chat_id=chat_id,
        workout_type=parsed["workout_type"],
        title=parsed["title"],
        duration_min=parsed["duration_min"],
        avg_rpe=parsed["avg_rpe"],
        exercises=parsed["exercises"],
        metrics=parsed["metrics"],
        source="text",
    )
    try:
        evaluation = await fitness.evaluate_workout(parsed, chat_id)
        fitness.update_workout_ai(workout_id, evaluation["ai_summary"], evaluation["ai_next_rec"])
    except Exception:
        logger.exception("fitness evaluate failed")
        evaluation = {"ai_summary": "", "ai_next_rec": {}}

    return {
        "id": workout_id,
        "entry": parsed,
        "evaluation": evaluation,
        "today": fitness.list_today(chat_id),
    }


@router.post("/api/fitness/log-image")
async def log_image(
    file: UploadFile,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty image")
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 12 MB)")

    try:
        parsed = await fitness.parse_workout_image(data, file.content_type or "image/jpeg", chat_id)
    except Exception as exc:
        logger.exception("fitness log-image parse failed")
        raise HTTPException(status_code=502, detail=f"Could not analyze image: {exc}")

    workout_id = fitness.insert_workout(
        chat_id=chat_id,
        workout_type=parsed["workout_type"],
        title=parsed["title"],
        duration_min=parsed["duration_min"],
        avg_rpe=parsed["avg_rpe"],
        exercises=parsed["exercises"],
        metrics=parsed["metrics"],
        source="image",
    )
    try:
        evaluation = await fitness.evaluate_workout(parsed, chat_id)
        fitness.update_workout_ai(workout_id, evaluation["ai_summary"], evaluation["ai_next_rec"])
    except Exception:
        logger.exception("fitness evaluate failed")
        evaluation = {"ai_summary": "", "ai_next_rec": {}}

    return {
        "id": workout_id,
        "entry": parsed,
        "evaluation": evaluation,
        "today": fitness.list_today(chat_id),
    }


@router.post("/api/fitness/log-structured")
async def log_structured(
    body: StructuredWorkout,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    workout_id = fitness.insert_workout(
        chat_id=chat_id,
        workout_type=body.workout_type,
        title=body.title,
        duration_min=body.duration_min,
        avg_rpe=body.avg_rpe,
        exercises=body.exercises,
        metrics=body.metrics,
        source=body.source,
    )
    workout = fitness.get_workout(workout_id, chat_id)
    try:
        evaluation = await fitness.evaluate_workout(workout or {}, chat_id)
        fitness.update_workout_ai(workout_id, evaluation["ai_summary"], evaluation["ai_next_rec"])
    except Exception:
        logger.exception("fitness evaluate failed")
        evaluation = {"ai_summary": "", "ai_next_rec": {}}

    return {
        "id": workout_id,
        "evaluation": evaluation,
        "today": fitness.list_today(chat_id),
    }


@router.post("/api/fitness/suggest")
async def suggest(
    body: SuggestRequest,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    try:
        plan = await fitness.suggest_workout(chat_id, minutes=body.minutes, workout_type=body.workout_type)
    except Exception as exc:
        logger.exception("fitness suggest failed")
        raise HTTPException(status_code=502, detail=f"Could not generate workout: {exc}")
    return plan


@router.post("/api/fitness/evaluate")
async def evaluate(
    body: EvaluateRequest,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    workout = fitness.get_workout(body.workout_id, chat_id)
    if not workout:
        raise HTTPException(status_code=404, detail="Workout not found")
    try:
        evaluation = await fitness.evaluate_workout(workout, chat_id)
        fitness.update_workout_ai(body.workout_id, evaluation["ai_summary"], evaluation["ai_next_rec"])
    except Exception as exc:
        logger.exception("fitness evaluate failed")
        raise HTTPException(status_code=502, detail=f"Could not evaluate: {exc}")
    return evaluation


@router.get("/api/fitness/morning-brief")
async def morning_brief(chat_id: str = Query(...), _: str = Depends(_require_token)):
    try:
        brief = await fitness.generate_morning_brief(chat_id)
    except Exception as exc:
        logger.exception("fitness morning brief failed")
        raise HTTPException(status_code=502, detail=f"Could not generate brief: {exc}")
    return {"brief": brief}


@router.get("/api/fitness/body-metrics")
async def get_body_metrics(
    chat_id: str = Query(...),
    days: int = Query(180, ge=1, le=365),
    _: str = Depends(_require_token),
):
    return {
        "latest": fitness.latest_body_metrics(chat_id),
        "history": fitness.body_metrics_history(chat_id, days),
    }


@router.post("/api/fitness/body-metrics")
async def post_body_metrics(
    body: BodyMetricsRequest,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    if all(v is None for v in [body.weight_kg, body.lbm_kg, body.smm_kg, body.bf_pct]):
        raise HTTPException(status_code=400, detail="At least one metric is required")
    metric_id = fitness.insert_body_metric(
        chat_id=chat_id,
        weight_kg=body.weight_kg,
        lbm_kg=body.lbm_kg,
        smm_kg=body.smm_kg,
        bf_pct=body.bf_pct,
        note=body.note,
        source="manual",
    )
    return {"id": metric_id, "latest": fitness.latest_body_metrics(chat_id)}


@router.get("/api/fitness/progression")
async def get_progression(
    chat_id: str = Query(...),
    exercise: str = Query(...),
    days: int = Query(180, ge=1, le=365),
    _: str = Depends(_require_token),
):
    return {"progression": fitness.exercise_progression(chat_id, exercise, days)}


@router.delete("/api/fitness/body-metrics/{metric_id}")
async def delete_metric(
    metric_id: int,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    if not fitness.delete_body_metric(metric_id, chat_id):
        raise HTTPException(status_code=404, detail="Metric not found")
    return {"ok": True}


@router.delete("/api/fitness/{workout_id}")
async def delete_entry(
    workout_id: int,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    if not fitness.delete_workout(workout_id, chat_id):
        raise HTTPException(status_code=404, detail="Workout not found")
    return {"ok": True}

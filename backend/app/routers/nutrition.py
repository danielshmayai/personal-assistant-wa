"""Nutrition API — log meals (image/text), today's totals, and history."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import TEST_TOKEN
from app import nutrition

logger = logging.getLogger("pa.nutrition")
router = APIRouter()
_bearer = HTTPBearer(auto_error=False)

_MAX_IMAGE_BYTES = 12 * 1024 * 1024  # 12 MB


def _require_token(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if not TEST_TOKEN or not creds or creds.credentials != TEST_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return creds.credentials


class TextLog(BaseModel):
    text: str

class WaterLog(BaseModel):
    amount_ml: int


@router.post("/api/nutrition/log-image")
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
        parsed = await nutrition.parse_meal_image(data, file.content_type or "image/jpeg")
    except Exception as exc:
        logger.exception("log-image parse failed")
        raise HTTPException(status_code=502, detail=f"Could not analyze image: {exc}")

    log_id = nutrition.insert_log(
        chat_id, parsed["meal_description"], parsed["protein"], parsed["carbs"],
        parsed["calories"], parsed["micros"], source="image",
    )
    return {"id": log_id, "entry": parsed, "today": nutrition.list_today(chat_id)}


@router.post("/api/nutrition/log-text")
async def log_text(
    body: TextLog,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty meal text")

    try:
        parsed = await nutrition.parse_meal_text(text)
    except Exception as exc:
        logger.exception("log-text parse failed")
        raise HTTPException(status_code=502, detail=f"Could not analyze meal: {exc}")

    log_id = nutrition.insert_log(
        chat_id, parsed["meal_description"], parsed["protein"], parsed["carbs"],
        parsed["calories"], parsed["micros"], source="text",
    )
    return {"id": log_id, "entry": parsed, "today": nutrition.list_today(chat_id)}


@router.post("/api/nutrition/log-water")
async def log_water(
    body: WaterLog,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    if body.amount_ml <= 0:
        raise HTTPException(status_code=400, detail="amount_ml must be positive")
    nutrition.log_water(chat_id, body.amount_ml)
    return {"today": nutrition.list_today(chat_id)}


@router.get("/api/nutrition/today")
async def today(chat_id: str = Query(...), _: str = Depends(_require_token)):
    return nutrition.list_today(chat_id)


@router.get("/api/nutrition/history")
async def get_history(
    chat_id: str = Query(...),
    days: int = Query(14, ge=1, le=90),
    _: str = Depends(_require_token),
):
    return {"days": nutrition.history(chat_id, days)}


@router.delete("/api/nutrition/{log_id}")
async def delete_entry(
    log_id: int,
    chat_id: str = Query(...),
    _: str = Depends(_require_token),
):
    if not nutrition.delete_log(log_id, chat_id):
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}

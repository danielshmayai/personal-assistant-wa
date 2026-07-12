"""Tenant-scoped nutrition API for the product web app.

Thin wrapper over the engine's nutrition data layer (app.nutrition). Auth is the
product session (require_module composes require_session → sets current_tenant_id,
enforces CSRF + rate limit + the 'nutrition' module toggle). The data functions
derive their scope from current_tenant_id, so the chat_id argument is vestigial —
we always pass "".
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app import nutrition
from product.deps import require_module
from product.tenancy.models import Tenant

logger = logging.getLogger("product.nutrition")

router = APIRouter(prefix="/api/nutrition")

_MAX_IMAGE_BYTES = 12 * 1024 * 1024  # 12 MB
_gate = require_module("nutrition")


class TextLog(BaseModel):
    text: str


class WaterLog(BaseModel):
    amount_ml: int


@router.get("/today")
async def today(_: Tenant = Depends(_gate)):
    return nutrition.list_today("")


@router.get("/history")
async def history(days: int = Query(14, ge=1, le=90), _: Tenant = Depends(_gate)):
    return {"days": nutrition.history("", days)}


@router.post("/log-text")
async def log_text(body: TextLog, _: Tenant = Depends(_gate)):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty_meal_text")
    try:
        parsed = await nutrition.parse_meal_text(text)
    except Exception as exc:
        logger.exception("log-text parse failed")
        raise HTTPException(status_code=502, detail=f"analyze_failed: {exc}")
    log_id = nutrition.insert_log(
        "", parsed["meal_description"], parsed["protein"], parsed["carbs"],
        parsed["calories"], parsed["micros"], source="text",
    )
    return {"id": log_id, "entry": parsed, "today": nutrition.list_today("")}


@router.post("/log-image")
async def log_image(file: UploadFile, _: Tenant = Depends(_gate)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty_image")
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="image_too_large")
    try:
        parsed = await nutrition.parse_meal_image(data, file.content_type or "image/jpeg")
    except Exception as exc:
        logger.exception("log-image parse failed")
        raise HTTPException(status_code=502, detail=f"analyze_failed: {exc}")
    log_id = nutrition.insert_log(
        "", parsed["meal_description"], parsed["protein"], parsed["carbs"],
        parsed["calories"], parsed["micros"], source="image",
    )
    return {"id": log_id, "entry": parsed, "today": nutrition.list_today("")}


@router.post("/log-water")
async def log_water(body: WaterLog, _: Tenant = Depends(_gate)):
    if body.amount_ml <= 0:
        raise HTTPException(status_code=400, detail="amount_ml_must_be_positive")
    # NOTE: unlike the owner's route, no Garmin push_hydration mirror — Garmin is
    # a single owner-global account and must not receive tenant data.
    nutrition.log_water("", body.amount_ml)
    return {"today": nutrition.list_today("")}


@router.get("/meal-suggestion")
async def meal_suggestion(_: Tenant = Depends(_gate)):
    try:
        return await nutrition.suggest_meals("")
    except Exception as exc:
        logger.exception("meal-suggestion failed")
        raise HTTPException(status_code=502, detail=f"suggestion_failed: {exc}")


@router.delete("/{log_id}")
async def delete_entry(log_id: int, _: Tenant = Depends(_gate)):
    if not nutrition.delete_log(log_id, ""):
        raise HTTPException(status_code=404, detail="entry_not_found")
    return {"ok": True}

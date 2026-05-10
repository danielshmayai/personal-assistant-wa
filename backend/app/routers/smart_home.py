"""Smart Home REST API — direct Tuya device control for the web UI."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import TEST_TOKEN, TUYA_ACCESS_ID, TUYA_PREFER_LOCAL

logger = logging.getLogger("pa.smart_home")
router = APIRouter(prefix="/api", tags=["smart_home"])


def _require_token(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
) -> str:
    if not TEST_TOKEN or credentials.credentials != TEST_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials.credentials


def _check_tuya() -> None:
    if not TUYA_ACCESS_ID:
        raise HTTPException(status_code=503, detail="Tuya not configured")


class ControlRequest(BaseModel):
    commands: dict[str, Any]


@router.get("/devices")
async def list_devices(_: str = Depends(_require_token)):
    _check_tuya()
    from app.tuya.tools import _fetch_devices
    try:
        devices = await asyncio.to_thread(_fetch_devices)
        return [
            {"id": d["id"], "name": d["name"], "category": d["category"], "online": d["online"]}
            for d in devices
        ]
    except Exception as exc:
        logger.exception("list_devices failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/devices/{device_id}/status")
async def get_status(device_id: str, _: str = Depends(_require_token)):
    _check_tuya()
    from app.tuya.tools import _fetch_status_local, _fetch_status_cloud
    try:
        status = None
        if TUYA_PREFER_LOCAL:
            status = await asyncio.to_thread(_fetch_status_local, device_id)
        if status is None:
            status = await asyncio.to_thread(_fetch_status_cloud, device_id)
        return status
    except Exception as exc:
        logger.exception("get_status failed for %s", device_id)
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/devices/{device_id}/control")
async def control_device_endpoint(
    device_id: str, req: ControlRequest, _: str = Depends(_require_token)
):
    _check_tuya()
    if not req.commands:
        raise HTTPException(status_code=400, detail="commands cannot be empty")
    from app.tuya.tools import _send_command_local, _send_command_cloud
    try:
        result = None
        if TUYA_PREFER_LOCAL:
            result = await asyncio.to_thread(_send_command_local, device_id, req.commands)
        if result is None:
            result = await asyncio.to_thread(_send_command_cloud, device_id, req.commands)
        return {"ok": True, "result": result}
    except Exception as exc:
        logger.exception("control_device failed for %s", device_id)
        raise HTTPException(status_code=502, detail=str(exc))

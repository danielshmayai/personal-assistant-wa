"""Tenant-scoped Smart Home (Tuya) API for the product web app.

Thin wrapper over app.tuya.tools, which is already fully tenant-scoped (P2
hardening): credentials resolve per-tenant via runtime.get_secret with a
per-tenant client cache, LAN control is owner-only, and a tenant with no
configured Tuya credentials gets a clean "not configured" error rather than
falling back to the owner's devices.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from product.deps import require_module
from product.tenancy.models import Tenant

logger = logging.getLogger("product.smart_home")
router = APIRouter(prefix="/api", tags=["smart_home"])
_gate = require_module("smart-home")


class ControlRequest(BaseModel):
    commands: dict[str, Any]


@router.get("/devices")
async def list_devices(_: Tenant = Depends(_gate)):
    from app.tuya.tools import _fetch_devices
    try:
        devices = await asyncio.to_thread(_fetch_devices)
        return [
            {"id": d["id"], "name": d["name"], "category": d["category"], "online": d["online"]}
            for d in devices
        ]
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("list_devices failed")
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/devices/{device_id}/status")
async def get_status(device_id: str, _: Tenant = Depends(_gate)):
    from app.tuya.tools import _fetch_status_cloud, _fetch_status_local, _lan_allowed
    try:
        status = None
        if _lan_allowed():
            status = await asyncio.to_thread(_fetch_status_local, device_id)
        if status is None:
            status = await asyncio.to_thread(_fetch_status_cloud, device_id)
        return status
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("get_status failed for %s", device_id)
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/devices/{device_id}/control")
async def control_device_endpoint(
    device_id: str, req: ControlRequest, _: Tenant = Depends(_gate)
):
    if not req.commands:
        raise HTTPException(status_code=400, detail="commands cannot be empty")
    from app.tuya.tools import _lan_allowed, _send_command_cloud, _send_command_local
    try:
        result = None
        if _lan_allowed():
            result = await asyncio.to_thread(_send_command_local, device_id, req.commands)
        if result is None:
            result = await asyncio.to_thread(_send_command_cloud, device_id, req.commands)
        return {"ok": True, "result": result}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("control_device failed for %s", device_id)
        raise HTTPException(status_code=502, detail=str(exc))

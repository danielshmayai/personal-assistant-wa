"""Tuya smart-home tools for the PA agent.

Three tools:
- list_tuya_devices   — discover all devices on the account
- get_device_status   — read current state of one device
- control_device      — send DPS commands to a device

Local LAN control is attempted first when TUYA_PREFER_LOCAL=true,
with automatic fallback to the Cloud API on failure.
All blocking I/O runs via asyncio.to_thread so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache
from typing import Any

import tinytuya
from langchain_core.tools import tool

from app.config import (
    TUYA_ACCESS_ID,
    TUYA_ACCESS_KEY,
    TUYA_API_ENDPOINT,
    TUYA_PREFER_LOCAL,
)

logger = logging.getLogger("pa.tuya")


# ---------------------------------------------------------------------------
# Cloud client singleton
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _cloud() -> tinytuya.Cloud:
    """Return a cached Tuya Cloud client. Parses region from endpoint URL."""
    region = "eu"
    endpoint = TUYA_API_ENDPOINT.lower()
    for r in ("us", "eu", "cn", "in"):
        if f"tuya{r}" in endpoint:
            region = r
            break
    return tinytuya.Cloud(
        apiRegion=region,
        apiKey=TUYA_ACCESS_ID,
        apiSecret=TUYA_ACCESS_KEY,
    )


# ---------------------------------------------------------------------------
# Sync helpers (called via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _fetch_devices() -> list[dict[str, Any]]:
    raw = _cloud().getdevices(verbose=True)
    if isinstance(raw, dict):
        if not raw.get("success", False):
            raise RuntimeError(f"Tuya Cloud error {raw.get('code')}: {raw.get('msg', raw)}")
        raw = raw.get("result", [])
    if not isinstance(raw, list):
        raise RuntimeError(f"Unexpected Tuya response: {raw!r}")
    return [
        {
            "id": d.get("id", ""),
            "name": d.get("name", "unknown"),
            "category": d.get("category", ""),
            "online": d.get("online", False),
            "ip": d.get("ip", ""),
            "local_key": d.get("local_key", ""),
            "version": str(d.get("version") or "3.3"),
        }
        for d in raw
    ]


def _fetch_status_cloud(device_id: str) -> dict[str, Any]:
    resp = _cloud().getstatus(device_id)
    if isinstance(resp, dict) and "result" in resp:
        result = resp["result"]
        if isinstance(result, list):
            return {item["code"]: item["value"] for item in result}
        return result  # type: ignore[return-value]
    raise RuntimeError(f"Unexpected status response: {resp!r}")


def _fetch_status_local(device_id: str) -> dict[str, Any] | None:
    """Try LAN status. Returns None on any failure."""
    try:
        devices = _fetch_devices()
        dev_info = next((d for d in devices if d["id"] == device_id), None)
        if not dev_info or not dev_info.get("ip"):
            return None
        dev = tinytuya.Device(
            dev_id=device_id,
            address=dev_info["ip"],
            local_key=dev_info["local_key"],
            version=float(dev_info["version"]),
            connection_timeout=4,
        )
        data = dev.status()
        if data and "dps" in data:
            return data["dps"]  # type: ignore[return-value]
    except Exception as exc:
        logger.debug("Local status failed for %s: %s", device_id, exc)
    return None


def _send_command_cloud(device_id: str, commands: dict[str, Any]) -> dict[str, Any]:
    payload = [{"code": k, "value": v} for k, v in commands.items()]
    resp = _cloud().sendcommand(device_id, {"commands": payload})
    return resp if isinstance(resp, dict) else {"raw": resp}


def _send_command_local(device_id: str, commands: dict[str, Any]) -> dict[str, Any] | None:
    """Try LAN control. Returns None on any failure."""
    try:
        devices = _fetch_devices()
        dev_info = next((d for d in devices if d["id"] == device_id), None)
        if not dev_info or not dev_info.get("ip"):
            return None
        dev = tinytuya.Device(
            dev_id=device_id,
            address=dev_info["ip"],
            local_key=dev_info["local_key"],
            version=float(dev_info["version"]),
            connection_timeout=4,
        )
        result = dev.set_multiple_values(commands)
        return {"result": result}
    except Exception as exc:
        logger.debug("Local control failed for %s: %s", device_id, exc)
    return None


# ---------------------------------------------------------------------------
# LangChain tools
# ---------------------------------------------------------------------------

@tool
async def list_tuya_devices() -> str:
    """List all Tuya smart-home devices (lights, switches, etc.) on the account."""
    try:
        devices = await asyncio.to_thread(_fetch_devices)
        safe = [{"id": d["id"], "name": d["name"], "category": d["category"], "online": d["online"]} for d in devices]
        return json.dumps(safe, ensure_ascii=False)
    except Exception as exc:
        logger.exception("list_tuya_devices failed")
        return f"Error listing devices: {exc}"


@tool
async def get_device_status(device_id: str) -> str:
    """Get current status of a Tuya device. Args: device_id (from list_tuya_devices)."""
    try:
        status: dict[str, Any] | None = None
        if TUYA_PREFER_LOCAL:
            status = await asyncio.to_thread(_fetch_status_local, device_id)
        if status is None:
            status = await asyncio.to_thread(_fetch_status_cloud, device_id)
        return json.dumps(status, ensure_ascii=False)
    except Exception as exc:
        logger.exception("get_device_status failed for %s", device_id)
        return f"Error getting status for {device_id!r}: {exc}"


@tool
async def control_device(device_id: str, commands: dict[str, Any]) -> str:
    """Control a Tuya device. Args: device_id, commands (DPS dict).
    Examples: turn on={"switch_1":true}, brightness={"bright_value":512}, colour temp={"temp_value":400}.
    Tries local LAN first, falls back to cloud."""
    if not commands:
        return "No commands provided."
    try:
        result: dict[str, Any] | None = None
        if TUYA_PREFER_LOCAL:
            result = await asyncio.to_thread(_send_command_local, device_id, commands)
        if result is None:
            result = await asyncio.to_thread(_send_command_cloud, device_id, commands)
        success = result.get("success", result.get("result", result))
        return f"Done. Response: {json.dumps(success, ensure_ascii=False)}"
    except Exception as exc:
        logger.exception("control_device failed for %s", device_id)
        return f"Error controlling {device_id!r}: {exc}"


def get_tuya_tools() -> list:
    """Return Tuya tools. Only included when credentials are configured."""
    if not TUYA_ACCESS_ID or not TUYA_ACCESS_KEY:
        return []
    return [list_tuya_devices, get_device_status, control_device]

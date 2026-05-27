"""Ecovacs REST API client — no XMPP/MQTT dependency, pure httpx.

Supports modern Ecovacs devices (DEEBOT X50 Ultra and similar) via the
global REST API at gl-{continent}-api.ecovacs.com.

Auth token is cached in-process; call invalidate_token() to force re-login.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import (
    ECOVACS_CONTINENT,
    ECOVACS_COUNTRY,
    ECOVACS_EMAIL,
    ECOVACS_PASSWORD,
)

logger = logging.getLogger("pa.ecovacs")

# Known public constants from the Ecovacs mobile app
_APP_CODE   = "global_e"
_APP_VER    = "1.6.3"
_APP_SECRET = "19cf5e50-2f12-4e33-8da0-6b4c38bab9ea"
_CHANNEL    = "google_play"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _auth_sign(params: dict[str, str]) -> str:
    """HMAC-style signature: MD5(SECRET + sorted_kv_concat + SECRET)."""
    keys = sorted(params)
    msg = "".join(f"{k}={params[k]}" for k in keys)
    return _md5(f"{_APP_SECRET}{msg}{_APP_SECRET}")


@dataclass
class _Token:
    uid: str
    access_token: str
    user_id: str
    fetched_at: float = field(default_factory=time.time)

    def expired(self) -> bool:
        # Ecovacs tokens are valid ~7 days; refresh after 6 hours to be safe
        return (time.time() - self.fetched_at) > 6 * 3600


_cached_token: _Token | None = None
_device_id = _md5(uuid.uuid4().hex).upper()


def invalidate_token() -> None:
    global _cached_token
    _cached_token = None


# ---------------------------------------------------------------------------
# REST calls
# ---------------------------------------------------------------------------

def _login_sync() -> _Token:
    global _cached_token

    ts = str(int(time.time() * 1000))
    req_id = uuid.uuid4().hex

    params: dict[str, str] = {
        "appCode":      _APP_CODE,
        "appVersion":   _APP_VER,
        "channel":      _CHANNEL,
        "country":      ECOVACS_COUNTRY.upper(),
        "deviceId":     _device_id,
        "deviceType":   "1",
        "email":        ECOVACS_EMAIL,
        "lang":         "en",
        "openid":       "NO",
        "password":     _md5(ECOVACS_PASSWORD),
        "requestId":    req_id,
        "authTimespan": ts,
    }
    params["authSign"] = _auth_sign(params)

    url = f"https://gl-{ECOVACS_CONTINENT}-api.ecovacs.com/v1/private/user/login"
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, data=params)
        resp.raise_for_status()
        data = resp.json()

    if str(data.get("code", "")) != "0000":
        raise RuntimeError(f"Ecovacs login failed: {data.get('msg', data)}")

    r = data["result"]
    tok = _Token(uid=r["uid"], access_token=r["accessToken"], user_id=r.get("userId", r["uid"]))
    _cached_token = tok
    logger.info("Ecovacs login OK uid=%s", tok.uid)
    return tok


def _get_token() -> _Token:
    if _cached_token is None or _cached_token.expired():
        return _login_sync()
    return _cached_token


def _api_headers(token: _Token) -> dict[str, str]:
    return {
        "Authorization":     f"Bearer {token.access_token}",
        "Content-Type":      "application/json",
        "Accept":            "application/json",
    }


def get_devices_sync() -> list[dict[str, Any]]:
    """Return list of Ecovacs devices on the account."""
    tok = _get_token()
    url = f"https://gl-{ECOVACS_CONTINENT}-api.ecovacs.com/v1/private/userdevice/device/getDeviceList"
    payload = {
        "userid":        tok.uid,
        "auth": {
            "userid":    tok.uid,
            "token":     tok.access_token,
            "realm":     "ecouser.net",
            "resource":  _device_id,
        },
    }
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json=payload, headers=_api_headers(tok))
        resp.raise_for_status()
        data = resp.json()

    if str(data.get("code", "")) not in ("0000", "0"):
        raise RuntimeError(f"getDeviceList failed: {data.get('msg', data)}")

    devices = data.get("result", {}).get("devices", data.get("result", []))
    if isinstance(devices, dict):
        devices = list(devices.values())
    return devices


def _first_device() -> dict[str, Any]:
    """Return the first (or only) vacuum on the account."""
    from app.config import ECOVACS_DEVICE_SERIAL
    devs = get_devices_sync()
    if not devs:
        raise RuntimeError("No Ecovacs devices found on this account.")
    if ECOVACS_DEVICE_SERIAL:
        match = next(
            (d for d in devs
             if d.get("did") == ECOVACS_DEVICE_SERIAL
             or ECOVACS_DEVICE_SERIAL.lower() in (d.get("name") or "").lower()),
            None,
        )
        if match:
            return match
    return devs[0]


def send_command_sync(cmd: str, payload: dict | None = None) -> dict[str, Any]:
    """Send a command to the primary Ecovacs vacuum via REST portal API."""
    tok   = _get_token()
    dev   = _first_device()
    did   = dev.get("did") or dev.get("deviceId", "")
    class_ = dev.get("deviceClass") or dev.get("class", "")

    ts = str(int(time.time() * 1000))
    body: dict[str, Any] = {
        "did":         did,
        "toId":        did,
        "toType":      class_ or "p2p",
        "td":          "q",
        "payloadType": "j",
        "payload":     json.dumps({
            "header": {"pri": 1, "ts": ts, "tzm": 120, "ver": "0.0.50"},
            "body":   {"cmd": cmd, **(payload or {})},
        }),
        "auth": {
            "userid":   tok.uid,
            "token":    tok.access_token,
            "realm":    "ecouser.net",
            "resource": _device_id,
        },
    }

    url = f"https://portal-{ECOVACS_CONTINENT}.ecouser.net/api/iot/devmanager.do"
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json=body, headers=_api_headers(tok))
        resp.raise_for_status()
        return resp.json()


def get_status_sync() -> dict[str, Any]:
    """Return device info + last-known status from the device list."""
    dev = _first_device()
    return {
        "name":       dev.get("name", "Unknown"),
        "did":        dev.get("did") or dev.get("deviceId", ""),
        "model":      dev.get("deviceName") or dev.get("model") or dev.get("class", ""),
        "online":     dev.get("online", False),
        "status":     dev.get("cleanState") or dev.get("status") or "unknown",
        "battery":    dev.get("battery"),
    }

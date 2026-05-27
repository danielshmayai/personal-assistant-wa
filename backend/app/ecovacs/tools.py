"""LangChain tools for Ecovacs robot vacuum control."""
from __future__ import annotations

import asyncio
import logging

from langchain_core.tools import tool

from app.config import ECOVACS_EMAIL, ECOVACS_PASSWORD

logger = logging.getLogger("pa.ecovacs")


def _configured() -> bool:
    return bool(ECOVACS_EMAIL and ECOVACS_PASSWORD)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
async def ecovacs_clean() -> str:
    """Start or resume automatic cleaning with the Ecovacs robot vacuum."""
    if not _configured():
        return "Ecovacs is not configured. Add ECOVACS_EMAIL and ECOVACS_PASSWORD to .env."
    try:
        from app.ecovacs.client import send_command_sync
        result = await asyncio.to_thread(
            send_command_sync,
            "clean_V2",
            {"act": "start", "type": "auto", "speed": 0, "content": [], "count": 0},
        )
        logger.info("ecovacs_clean result: %s", result)
        return "✅ Robot vacuum started cleaning."
    except Exception as exc:
        logger.exception("ecovacs_clean failed")
        return f"❌ Failed to start cleaning: {exc}"


@tool
async def ecovacs_stop() -> str:
    """Stop/pause the Ecovacs robot vacuum."""
    if not _configured():
        return "Ecovacs is not configured. Add ECOVACS_EMAIL and ECOVACS_PASSWORD to .env."
    try:
        from app.ecovacs.client import send_command_sync
        result = await asyncio.to_thread(
            send_command_sync,
            "clean_V2",
            {"act": "stop"},
        )
        logger.info("ecovacs_stop result: %s", result)
        return "✅ Robot vacuum stopped."
    except Exception as exc:
        logger.exception("ecovacs_stop failed")
        return f"❌ Failed to stop: {exc}"


@tool
async def ecovacs_charge() -> str:
    """Send the Ecovacs robot vacuum back to its charging dock."""
    if not _configured():
        return "Ecovacs is not configured. Add ECOVACS_EMAIL and ECOVACS_PASSWORD to .env."
    try:
        from app.ecovacs.client import send_command_sync
        result = await asyncio.to_thread(
            send_command_sync,
            "charge",
            {"act": "go"},
        )
        logger.info("ecovacs_charge result: %s", result)
        return "✅ Robot vacuum returning to dock."
    except Exception as exc:
        logger.exception("ecovacs_charge failed")
        return f"❌ Failed to send to dock: {exc}"


@tool
async def ecovacs_status() -> str:
    """Get the current status of the Ecovacs robot vacuum (battery, cleaning state, online)."""
    if not _configured():
        return "Ecovacs is not configured. Add ECOVACS_EMAIL and ECOVACS_PASSWORD to .env."
    try:
        from app.ecovacs.client import get_status_sync
        st = await asyncio.to_thread(get_status_sync)
        battery = f"{st['battery']}%" if st.get("battery") is not None else "unknown"
        online  = "🟢 online" if st.get("online") else "🔴 offline"
        status  = st.get("status", "unknown")
        name    = st.get("name", "Robot vacuum")
        model   = st.get("model", "")
        model_str = f" ({model})" if model else ""
        return (
            f"🤖 **{name}**{model_str}\n"
            f"• Status: {status}\n"
            f"• Battery: {battery}\n"
            f"• Connection: {online}"
        )
    except Exception as exc:
        logger.exception("ecovacs_status failed")
        return f"❌ Failed to get status: {exc}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def get_ecovacs_tools() -> list:
    """Return Ecovacs tools. Returns empty list if credentials not set."""
    if not _configured():
        return []
    return [ecovacs_clean, ecovacs_stop, ecovacs_charge, ecovacs_status]

"""Multi-channel notification manager.

Maintains a registry of active WebSocket sessions (web UI) and provides a
unified broadcast that pushes to both web clients and WhatsApp simultaneously.

Usage from anywhere in the backend:
    from app.broadcast import NotificationManager
    await NotificationManager.broadcast(
        message="Done! Saved to vault.",
        whatsapp_chat_ids=["972501234567@c.us"],
    )
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger("pa.broadcast")


class NotificationManager:
    """Singleton registry + broadcaster.

    Web sessions self-register on WebSocket connect and unregister on
    disconnect (handled by web_chat.py).  WhatsApp targets are passed
    explicitly — the manager never stores phone numbers.
    """

    # chat_id → WebSocket for every active web session
    _connections: dict[str, "WebSocket"] = {}

    # ── Registration ────────────────────────────────────────────────────────

    @classmethod
    def register(cls, chat_id: str, ws: "WebSocket") -> None:
        cls._connections[chat_id] = ws
        logger.debug("WS registered: %s (total=%d)", chat_id, len(cls._connections))

    @classmethod
    def unregister(cls, chat_id: str) -> None:
        cls._connections.pop(chat_id, None)
        logger.debug("WS unregistered: %s (total=%d)", chat_id, len(cls._connections))

    @classmethod
    def active_web_sessions(cls) -> list[str]:
        return list(cls._connections.keys())

    # ── Push helpers ────────────────────────────────────────────────────────

    @classmethod
    async def push_web(
        cls,
        event: dict,
        chat_id: str | None = None,
    ) -> None:
        """Push a JSON event to one specific session or all active sessions."""
        if chat_id:
            targets = {chat_id: cls._connections[chat_id]} if chat_id in cls._connections else {}
        else:
            targets = dict(cls._connections)

        payload = json.dumps(event)
        dead: list[str] = []
        for cid, ws in targets.items():
            try:
                await ws.send_text(payload)
            except Exception as exc:
                logger.debug("WS send failed for %s: %s", cid, exc)
                dead.append(cid)

        for cid in dead:
            cls._connections.pop(cid, None)

    @classmethod
    async def push_whatsapp(cls, chat_ids: list[str], message: str) -> None:
        """Send a text message to one or more WhatsApp chats via WAHA."""
        from app.whatsapp import send_whatsapp_message

        results = await asyncio.gather(
            *[send_whatsapp_message(cid, message) for cid in chat_ids],
            return_exceptions=True,
        )
        for cid, res in zip(chat_ids, results):
            if isinstance(res, Exception):
                logger.warning("WA push failed for %s: %s", cid, res)

    # ── Unified broadcast ───────────────────────────────────────────────────

    @classmethod
    async def broadcast(
        cls,
        message: str,
        whatsapp_chat_ids: list[str] | None = None,
        web_chat_id: str | None = None,
    ) -> None:
        """Broadcast a plain-text message to web sessions and/or WhatsApp.

        Args:
            message:            The text to deliver.
            whatsapp_chat_ids:  WAHA chat IDs to send to (e.g. ["972501234567@c.us"]).
                                Pass None to skip WhatsApp.
            web_chat_id:        Specific web session to target.
                                Pass None to push to ALL active web sessions.
        """
        tasks: list = []

        if cls._connections:
            tasks.append(
                cls.push_web(
                    {"type": "notification", "message": message},
                    chat_id=web_chat_id,
                )
            )

        if whatsapp_chat_ids:
            tasks.append(cls.push_whatsapp(whatsapp_chat_ids, message))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

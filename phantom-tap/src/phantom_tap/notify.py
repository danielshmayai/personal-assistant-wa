"""Telling you what happened.

Reuses the WhatsApp channel the personal assistant already runs (WAHA), rather
than standing up a second bot: the send is one HTTP call to a service that is
already on this network and already paired with your phone.

A notification failure is never allowed to change a booking outcome. By the time
we are notifying, the race is decided and recorded; an unreachable WAHA is a
logging problem, not a booking problem.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from phantom_tap.config import NotifyConfig

logger = logging.getLogger("phantom_tap.notify")


class Notifier(Protocol):
    async def send(self, text: str) -> bool: ...


class LogNotifier:
    async def send(self, text: str) -> bool:
        logger.info("notify: %s", text.replace("\n", " | "))
        return True


class NullNotifier:
    async def send(self, text: str) -> bool:
        return True


class WahaNotifier:
    """POST /api/sendText against the assistant's existing WAHA container."""

    def __init__(self, url: str, session: str, chat_id: str, timeout: float = 10.0) -> None:
        self.url = url.rstrip("/")
        self.session = session
        self.chat_id = chat_id
        self.timeout = timeout

    async def send(self, text: str) -> bool:
        payload = {"session": self.session, "chatId": self.chat_id, "text": text}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.url}/api/sendText", json=payload)
            if response.status_code >= 400:
                logger.error("waha refused the message: HTTP %s %s", response.status_code, response.text[:200])
                return False
            return True
        except httpx.HTTPError as exc:
            logger.error("waha unreachable at %s (%s) - the booking result stands", self.url, exc)
            return False


def build(config: NotifyConfig) -> Notifier:
    if config.backend == "waha":
        return WahaNotifier(config.waha_url, config.waha_session, config.chat_id)
    if config.backend == "none":
        return NullNotifier()
    return LogNotifier()


def format_result(label: str, result, *, overshoot_ms: float, offset_ms: float) -> str:
    """The message a human actually wants at 14:10:03."""
    if result.won:
        head = f"✅ נרשמת ל{label}"
        if result.seat is not None:
            head += f" · מקום {result.seat}"
    elif result.registered:
        head = f"⚠️ נרשמת ל{label} אבל האישור לא אומת"
    else:
        head = f"❌ לא נרשמת ל{label}"

    lines = [head]
    if result.reason:
        lines.append(result.reason)
    if result.attempts:
        first = result.attempts[0]
        lines.append(
            f"ניסיון ראשון T0{first.fired_at * 1000:+.0f}ms · {len(result.attempts)} ניסיונות · "
            f"סטיית שעון {offset_ms:+.0f}ms · חריגה {overshoot_ms:.1f}ms"
        )
    return "\n".join(lines)

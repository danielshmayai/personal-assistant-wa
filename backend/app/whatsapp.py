import logging
from fastapi import APIRouter, Query, Request, Response
import httpx
from app.config import WAHA_BASE_URL, WAHA_API_KEY, WAHA_SESSION, MY_WHATSAPP_ID, WEBHOOK_SECRET

logger = logging.getLogger("pa.whatsapp")
router = APIRouter()

BOT_TRIGGERS = ("@danidin", "!danidin")


async def send_whatsapp_message(chat_id: str, text: str) -> bool:
    """Send a text message via WAHA API. Returns True on success."""
    headers = {}
    if WAHA_API_KEY:
        headers["X-Api-Key"] = WAHA_API_KEY
    payload = {
        "chatId": chat_id,
        "text": text,
        "session": WAHA_SESSION,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.post(
                f"{WAHA_BASE_URL}/api/sendText",
                json=payload,
                headers=headers,
            )
            if r.status_code in (200, 201):
                return True
            logger.error("WAHA sendText failed: %s %s", r.status_code, r.text)
            return False
        except Exception:
            logger.exception("Failed to send WhatsApp message")
            return False


def _is_self_chat(body: dict) -> bool:
    """Self-chat: fromMe=True and destination is own number or own LID (not a group).
    Newer WhatsApp clients use @lid format instead of @c.us for the self-device identifier."""
    payload = body.get("payload", {})
    from_me = payload.get("fromMe", False)
    if not from_me:
        return False
    to = payload.get("to", "")
    return to == MY_WHATSAPP_ID or to.endswith("@lid")


def _is_group(body: dict) -> bool:
    """Group chat: destination ends with @g.us."""
    payload = body.get("payload", {})
    to = payload.get("to", "")
    return to.endswith("@g.us")


def _extract_text(body: dict) -> str:
    """Extract the plain text content from a WAHA webhook payload."""
    payload = body.get("payload", {})
    return payload.get("body", "").strip()


def _extract_chat_id(body: dict) -> str:
    """Extract the chat ID to reply to (always the 'to' field — group or self)."""
    payload = body.get("payload", {})
    return payload.get("to", "")


@router.post("/webhook/waha")
async def waha_webhook(request: Request, secret: str = Query(default="")):
    """
    WAHA webhook receiver with strict routing:
    - Self-chat: route raw text to LangGraph pipeline.
    - Group chat: IGNORE unless message starts with @danidin or !danidin.
    - DMs from others: IGNORE completely.
    """
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        logger.warning("Webhook rejected: invalid or missing secret (remote=%s)", request.client)
        return Response(status_code=403)

    body = await request.json()
    event = body.get("event", "")
    payload = body.get("payload", {})
    logger.info("Webhook event=%s to=%s fromMe=%s id=%s body=%.60s",
                event, payload.get("to"), payload.get("fromMe"), payload.get("id"), payload.get("body", ""))

    # Accept both "message" and "message.any" (self-chat fires as message.any)
    if event not in ("message", "message.any"):
        return Response(status_code=200)

    # Skip other bot replies to avoid loops (fromMe=True but not self-chat or group)
    if payload.get("fromMe") and not _is_self_chat(body) and not _is_group(body):
        return Response(status_code=200)

    text = _extract_text(body)
    if not text:
        return Response(status_code=200)

    # Skip bot's own replies — always prefixed with [ *danidin* ] (LTR or RTL).
    # This replaces the fragile _3EB0 ID filter which also blocked WhatsApp Web messages.
    if text.startswith("[ *danidin* ]") or text.startswith("\u200f[ *danidin* ]"):
        return Response(status_code=200)

    chat_id = _extract_chat_id(body)

    # --- SELF-CHAT: command center ---
    if _is_self_chat(body):
        logger.info("Self-chat message: %.80s...", text)
        reply = await _process_message(text, chat_id)
        await send_whatsapp_message(chat_id, reply)
        return Response(status_code=200)

    # --- GROUP CHAT: only respond to @danidin / !danidin ---
    if _is_group(body):
        text_lower = text.lower()
        for trigger in BOT_TRIGGERS:
            if text_lower.startswith(trigger):
                stripped = text[len(trigger):].strip()
                if stripped:
                    logger.info("Group trigger [%s]: %.80s...", chat_id, stripped)
                    reply = await _process_message(stripped, chat_id)
                    await send_whatsapp_message(chat_id, reply)
                return Response(status_code=200)
        # No trigger — ignore silently.
        return Response(status_code=200)

    # --- DMs from others: ignore ---
    return Response(status_code=200)


def _is_rtl(text: str) -> bool:
    """Return True if the text contains Hebrew or Arabic characters."""
    return any("\u0590" <= c <= "\u05FF" or "\u0600" <= c <= "\u06FF" for c in text)


async def _process_message(text: str, chat_id: str) -> str:
    """Run text through the LangGraph pipeline. Returns the reply string."""
    # Import here to avoid circular imports at module load.
    from app.graph.graph import run_graph
    try:
        reply = await run_graph(text, chat_id)
    except Exception:
        logger.exception("Graph execution failed")
        reply = "[Error] Something went wrong processing your message."
    # For RTL replies (Hebrew/Arabic), prepend RLM so the prefix anchors to the right.
    prefix = "\u200f[ *danidin* ]" if _is_rtl(reply) else "[ *danidin* ]"
    return f"{prefix} {reply}"

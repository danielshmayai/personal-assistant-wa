import logging
from fastapi import APIRouter, Request, Response
import httpx
from app.config import WAHA_BASE_URL, WAHA_API_KEY, WAHA_SESSION, MY_WHATSAPP_ID

logger = logging.getLogger("pa.whatsapp")
router = APIRouter()

BOT_TRIGGERS = ("@bot", "!bot")


async def send_whatsapp_message(chat_id: str, text: str) -> bool:
    """Send a text message via WAHA API. Returns True on success."""
    headers = {}
    if WAHA_API_KEY:
        headers["Authorization"] = f"Bearer {WAHA_API_KEY}"
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
            if r.status_code == 200 or r.status_code == 201:
                return True
            logger.error("WAHA sendText failed: %s %s", r.status_code, r.text)
            return False
        except Exception:
            logger.exception("Failed to send WhatsApp message")
            return False


def _is_self_chat(body: dict) -> bool:
    """Check if this message is from 'Message Yourself' (self-chat)."""
    payload = body.get("payload", {})
    from_id = payload.get("from", "")
    to = payload.get("to", "")
    # In self-chat, both from and to are your own ID.
    return from_id == MY_WHATSAPP_ID and to == MY_WHATSAPP_ID


def _is_group(body: dict) -> bool:
    """Check if the message is from a group chat."""
    payload = body.get("payload", {})
    chat_id = payload.get("from", "")
    return chat_id.endswith("@g.us")


def _extract_text(body: dict) -> str:
    """Extract the plain text content from a WAHA webhook payload."""
    payload = body.get("payload", {})
    return payload.get("body", "").strip()


def _extract_chat_id(body: dict) -> str:
    """Extract the chat ID to reply to."""
    payload = body.get("payload", {})
    return payload.get("from", "")


@router.post("/webhook/waha")
async def waha_webhook(request: Request):
    """
    WAHA webhook receiver with strict routing:
    - Self-chat: route raw text to LangGraph pipeline.
    - Group chat: IGNORE unless message starts with @bot or !bot.
    - DMs from others: IGNORE completely.
    """
    body = await request.json()
    event = body.get("event", "")

    # Only process incoming messages.
    if event != "message":
        return Response(status_code=200)

    text = _extract_text(body)
    if not text:
        return Response(status_code=200)

    chat_id = _extract_chat_id(body)

    # --- SELF-CHAT: command center ---
    if _is_self_chat(body):
        logger.info("Self-chat message: %.80s...", text)
        reply = await _process_message(text, chat_id)
        await send_whatsapp_message(chat_id, reply)
        return Response(status_code=200)

    # --- GROUP CHAT: only respond to @bot / !bot ---
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


async def _process_message(text: str, chat_id: str) -> str:
    """Run text through the LangGraph pipeline. Returns the reply string."""
    # Import here to avoid circular imports at module load.
    from app.graph.graph import run_graph
    try:
        return await run_graph(text, chat_id)
    except Exception:
        logger.exception("Graph execution failed")
        return "[Error] Something went wrong processing your message."

import logging
from fastapi import APIRouter, Query, Request, Response
import httpx
from app.config import WAHA_BASE_URL, WAHA_API_KEY, WAHA_SESSION, MY_WHATSAPP_ID, MY_WHATSAPP_LID, WEBHOOK_SECRET

logger = logging.getLogger("pa.whatsapp")
router = APIRouter()

BOT_TRIGGERS = ("@danidin", "!danidin")

# Own @lid — populated at startup from WAHA API or from MY_WHATSAPP_LID config.
# Used to reliably detect self-chat when WhatsApp uses @lid instead of @c.us.
_own_lid: str = MY_WHATSAPP_LID


async def detect_own_lid() -> None:
    """Query WAHA for the current session's own @lid and cache it.

    In newer WhatsApp multi-device, the self-chat 'to' field uses @lid format
    instead of @c.us. Without knowing our own @lid we can't reliably tell
    self-chat from a DM to a contact who also has an @lid identifier.
    """
    global _own_lid
    if _own_lid:
        return  # already configured or previously detected
    headers: dict[str, str] = {}
    if WAHA_API_KEY:
        headers["X-Api-Key"] = WAHA_API_KEY
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{WAHA_BASE_URL}/api/sessions/{WAHA_SESSION}",
                headers=headers,
            )
            if r.status_code == 200:
                data = r.json()
                me = data.get("me") or {}
                # WAHA may return lid separately or inside 'id'
                for key in ("lid", "id", "_serialized"):
                    val = str(me.get(key, ""))
                    if val.endswith("@lid"):
                        _own_lid = val
                        logger.info("whatsapp: auto-detected own @lid = %s", _own_lid)
                        return
    except Exception as exc:
        logger.debug("whatsapp: could not auto-detect own @lid: %s", exc)


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
    """Detect self-chat (Saved Messages / Notes to self).

    Priority:
      1. to == MY_WHATSAPP_ID  — exact @c.us match (most reliable).
      2. to == _own_lid         — exact @lid match after auto-detection.
      3. from == to             — same JID, handles any format.
      4. @lid fallback          — when own @lid is unknown, assume any @lid
                                  destination is self-chat. May have false
                                  positives for contacts who use @lid; set
                                  MY_WHATSAPP_LID in .env to eliminate them.
    """
    payload = body.get("payload", {})
    if not payload.get("fromMe", False):
        return False
    to = payload.get("to", "")
    frm = payload.get("from", "")
    if MY_WHATSAPP_ID and to == MY_WHATSAPP_ID:
        return True
    if _own_lid and to == _own_lid:
        return True
    if frm and to and frm == to:
        return True
    # Fallback: if own @lid is not yet known, treat all @lid destinations as
    # potential self-chat. Once WAHA auto-detection succeeds this branch is
    # bypassed and only the exact match above fires.
    if not _own_lid and to.endswith("@lid"):
        return True
    return False


def _is_group(body: dict) -> bool:
    """Group chat: destination ends with @g.us."""
    payload = body.get("payload", {})
    to = payload.get("to", "")
    return to.endswith("@g.us")


def _extract_text(body: dict) -> str:
    """Extract the plain text content from a WAHA webhook payload."""
    payload = body.get("payload", {})
    return payload.get("body", "").strip()


# MIME type → default filename extension (when WAHA reports no filename)
_EXT_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "application/pdf": ".pdf",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
}

_TYPE_MIME_FALLBACK = {
    "image": "image/jpeg",
    "document": "application/octet-stream",
    "video": "video/mp4",
    "audio": "audio/ogg",
    "sticker": "image/webp",
}


def _extract_media_context(body: dict) -> str | None:
    """
    If the message carries media, return a compact context tag for the agent:
      [MEDIA id=<msgId> type=<image|document|…> filename=<name> mime=<type>]

    The agent uses 'id' to call drive_save_photo / drive_save_document.
    Returns None when no media is present.
    """
    payload = body.get("payload", {})
    if not payload.get("hasMedia"):
        return None

    message_id = payload.get("id", "unknown")
    msg_type = payload.get("type", "")

    # MIME type — try _data first, fall back to type-based guess
    data_field = payload.get("_data", {})
    mime_type = data_field.get("mimetype", "") or _TYPE_MIME_FALLBACK.get(msg_type, "application/octet-stream")

    # Filename — documents usually have one; photos often don't
    from datetime import datetime as _dt
    _ts = _dt.now().strftime("%Y%m%d_%H%M%S")
    _label = msg_type or "file"
    filename = (
        data_field.get("filename")
        or payload.get("filename")
        or f"{_label}_{_ts}{_EXT_MAP.get(mime_type, '')}"
    )

    return f"[MEDIA id={message_id} type={msg_type} filename={filename} mime={mime_type}]"


def _extract_chat_id(body: dict) -> str:
    """Extract the chat ID to reply to.

    Groups: always the @g.us ID (in 'to').
    Outgoing (fromMe): destination is the chat — use 'to'.
    Incoming DM: the sender IS the chat — use 'from'.
    """
    payload = body.get("payload", {})
    to = payload.get("to", "")
    if to.endswith("@g.us"):
        return to
    if payload.get("fromMe", False):
        return to
    return payload.get("from", "") or to


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

    text = _extract_text(body)
    media_ctx = _extract_media_context(body)

    # Skip if neither text nor media
    if not text and not media_ctx:
        return Response(status_code=200)

    # Skip bot's own replies — always prefixed with [ *danidin* ] (LTR or RTL).
    if text.startswith("[ *danidin* ]") or text.startswith("\u200f[ *danidin* ]"):
        return Response(status_code=200)

    chat_id = _extract_chat_id(body)

    # Cache media bytes from the webhook payload NOW (before the agent runs).
    if media_ctx:
        from app.media_cache import store_from_payload
        store_from_payload(payload.get("id", ""), payload)

    # Build the full message for the agent: media tag (if any) + caption/text
    if media_ctx:
        full_text = f"{media_ctx}\n{text}" if text else f"{media_ctx}\nPlease save this to Google Drive."
    else:
        full_text = text

    # --- SELF-CHAT: respond to everything ---
    if _is_self_chat(body):
        logger.info("Self-chat: %.80s...", full_text)
        reply = await _process_message(full_text, chat_id)
        await send_whatsapp_message(chat_id, reply)
        return Response(status_code=200)

    # --- GROUPS and DMs: respond only when @danidin / !danidin prefix is used ---
    text_lower = text.lower()
    for trigger in BOT_TRIGGERS:
        if text_lower.startswith(trigger):
            stripped = full_text[len(trigger):].strip()
            if stripped:
                logger.info("Trigger '%s' in chat %s: %.80s...", trigger, chat_id, stripped)
                reply = await _process_message(stripped, chat_id)
                await send_whatsapp_message(chat_id, reply)
            return Response(status_code=200)

    # No trigger and not self-chat — ignore.
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

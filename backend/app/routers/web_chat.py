"""
Web chat router — WebSocket streaming chat, conversations, and file upload.

Endpoints:
  WS  /ws/chat              — streaming chat  (?token=&chat_id=)
  GET /api/conversations    — list web conversations
  POST /api/upload          — upload a file into media_cache
  GET /api/tts              — text-to-speech via edge-tts (returns audio/mpeg)
"""

import contextlib
import json
import logging
import mimetypes
import os
import re
import tempfile
import uuid
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import TEST_TOKEN

logger = logging.getLogger("pa.web_chat")
router = APIRouter()

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_AUDIO_BYTES  = 25 * 1024 * 1024  # 25 MB

# Lazy-loaded Whisper model (loaded on first STT request)
_whisper = None


def _clean_for_tts(text: str) -> str:
    """Strip all non-speakable content so TTS reads only natural prose."""
    # Remove fenced code blocks entirely
    text = re.sub(r'```[\s\S]*?```', '', text)
    # Remove inline code
    text = re.sub(r'`[^`\n]+`', '', text)
    # Markdown links: keep label, drop URL
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # Bare URLs (http/https)
    text = re.sub(r'https?://\S+', '', text)
    # Obsidian wikilinks [[Label|Alias]] or [[Label]] → display text
    text = re.sub(r'\[\[(?:[^\]|]*\|)?([^\]]*)\]\]', r'\1', text)
    # Remaining square-bracket content (e.g. [1], [source])
    text = re.sub(r'\[[^\]]{0,60}\]', '', text)
    # Hashtags / Obsidian tags  (#real-estate, #tag)
    text = re.sub(r'(?<!\w)#\w[\w-]*', '', text)
    # Markdown headers — keep the heading text itself
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Bold / italic markers
    text = re.sub(r'\*{1,3}', '', text)
    text = re.sub(r'_{1,3}', '', text)
    # Strikethrough — delete entirely (it's crossed-out content)
    text = re.sub(r'~~[^~]*~~', '', text)
    # Blockquotes
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Table separators |---|
    text = re.sub(r'^\|[-| :]+\|$', '', text, flags=re.MULTILINE)
    # Table pipes → space
    text = re.sub(r'\|', ' ', text)
    # Bullet list markers at line start
    text = re.sub(r'^[ \t]*[-•*]\s+', '', text, flags=re.MULTILINE)
    # Numbered list markers
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # HTML tags
    text = re.sub(r'<[^>]{1,80}>', '', text)
    # Parentheses containing only a URL or non-word content
    text = re.sub(r'\(\s*https?://[^)]*\)', '', text)
    # Lone special chars that add no speech value
    text = re.sub(r'[`~^]', '', text)
    # Normalize whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def _get_whisper():
    global _whisper
    if _whisper is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError(
                "faster-whisper is not installed (it is available inside the Docker container). "
                "STT is not supported in this environment."
            )
        _whisper = WhisperModel("tiny", device="cpu", compute_type="int8")
        logger.info("Whisper tiny model loaded")
    return _whisper


# ── Auth ─────────────────────────────────────────────────────────────────────

def _verify_token(token: str) -> bool:
    # Fails closed: rejects all connections when TEST_TOKEN is not configured.
    return bool(TEST_TOKEN) and token == TEST_TOKEN


def _require_bearer(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
) -> str:
    if not _verify_token(credentials.credentials):
        raise HTTPException(status_code=403, detail="Invalid token")
    return credentials.credentials


# ── WebSocket /ws/chat ────────────────────────────────────────────────────────

@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket, token: str = "", chat_id: str = "web"):
    if not _verify_token(token):
        await websocket.accept()
        await websocket.close(code=4403, reason="Unauthorized")
        return

    # All web chat IDs must start with "web" so distiller detects the platform
    if not chat_id.startswith("web"):
        chat_id = "web"

    await websocket.accept()
    loop = asyncio.get_running_loop()
    logger.info("Web chat connected: chat_id=%s", chat_id)

    from app.broadcast import NotificationManager
    NotificationManager.register(chat_id, websocket)

    # Register / touch conversation record
    try:
        from app.memory.store import upsert_web_conversation
        await loop.run_in_executor(None, upsert_web_conversation, chat_id, None)
    except Exception:
        logger.exception("Failed to upsert conversation chat_id=%s", chat_id)

    # Send conversation history
    try:
        from app.graph.streaming import get_history
        history = await get_history(chat_id)
        await websocket.send_text(json.dumps({"type": "history", "messages": history}))
    except Exception:
        logger.exception("Failed to send history for chat_id=%s", chat_id)
        history = []

    # Deliver any notifications that fired while the WebSocket was closed
    try:
        from app.scheduled_jobs import fetch_pending_notifications, mark_notifications_delivered
        pending = await loop.run_in_executor(None, fetch_pending_notifications, chat_id)
        for msg in pending:
            await websocket.send_text(json.dumps({"type": "notification", "message": msg}))
        if pending:
            await loop.run_in_executor(None, mark_notifications_delivered, chat_id)
            logger.info("Delivered %d pending notifications to chat_id=%s", len(pending), chat_id)
    except Exception:
        logger.exception("Failed to deliver pending notifications for chat_id=%s", chat_id)

    # Only set title from first-ever message (if conversation has no history yet)
    title_pending = len(history) == 0

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
                continue

            if msg.get("type") != "message":
                continue

            user_text = (msg.get("text") or "").strip()
            media_id = msg.get("media_id")

            if not user_text and not media_id:
                continue

            # Build full text, prepending MEDIA tag if a file was uploaded
            if media_id:
                from app.media_cache import retrieve
                cached = retrieve(media_id)
                if cached:
                    mime = cached.get("mime_type", "application/octet-stream")
                    fname = cached.get("filename", f"upload_{media_id[:8]}")
                    broad = "image" if mime.startswith("image/") else "document"
                    media_tag = f"[MEDIA id={media_id} type={broad} filename={fname} mime={mime}]"
                    full_text = f"{media_tag}\n{user_text}" if user_text else f"{media_tag}\n(no caption — follow the media routing rules: image → analyze first, document → save)"
                else:
                    full_text = user_text or "Please save my file to Google Drive."
            else:
                full_text = user_text

            # Set conversation title from first user message
            if title_pending and user_text:
                title = user_text[:60].strip()
                try:
                    from app.memory.store import upsert_web_conversation
                    await loop.run_in_executor(None, upsert_web_conversation, chat_id, title)
                    await websocket.send_text(json.dumps({
                        "type": "conversation_updated",
                        "id": chat_id,
                        "title": title,
                    }))
                except Exception:
                    logger.exception("Failed to set conversation title")
                title_pending = False

            # Stream response
            try:
                from app.graph.streaming import stream_graph
                async for event in stream_graph(full_text, chat_id):
                    await websocket.send_text(json.dumps(event))
                # Touch updated_at after each completed exchange
                try:
                    from app.memory.store import upsert_web_conversation
                    await loop.run_in_executor(None, upsert_web_conversation, chat_id, None)
                except Exception:
                    pass
            except Exception as exc:
                import traceback as _tb
                _trace = _tb.format_exc()
                logger.exception("stream_graph failed for chat_id=%s", chat_id)
                await websocket.send_text(json.dumps({
                    "type": "token",
                    "content": (
                        f"⚠️ **{type(exc).__name__}**: `{exc}`\n\n"
                        f"```\n{_trace[-2000:]}\n```"
                    ),
                }))
                await websocket.send_text(json.dumps({"type": "done", "full_reply": str(exc)}))

    except WebSocketDisconnect:
        logger.info("Web chat disconnected: chat_id=%s", chat_id)
    finally:
        from app.broadcast import NotificationManager
        NotificationManager.unregister(chat_id)


# ── GET /api/conversations ────────────────────────────────────────────────────

@router.get("/api/conversations")
async def get_conversations(_: str = Depends(_require_bearer)):
    from app.memory.store import list_web_conversations
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, list_web_conversations)


# ── DELETE /api/conversations/{chat_id} ──────────────────────────────────────

@router.delete("/api/conversations/{chat_id}")
async def delete_conversation(chat_id: str, _: str = Depends(_require_bearer)):
    from app.memory.store import delete_web_conversation
    from app.graph.checkpointer import delete_thread_checkpoints
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, delete_web_conversation, chat_id)
    await delete_thread_checkpoints(chat_id)
    return {"ok": True}


# ── POST /api/upload ──────────────────────────────────────────────────────────

@router.post("/api/upload")
async def upload_file(file: UploadFile, _: str = Depends(_require_bearer)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 50 MB)")

    media_id = f"web_{uuid.uuid4().hex}"

    mime_type = file.content_type or ""
    if not mime_type or mime_type == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(file.filename or "")
        mime_type = guessed or "application/octet-stream"

    from app.media_cache import store_web_upload
    store_web_upload(media_id, data, mime_type, file.filename or media_id)

    logger.info("Web upload: media_id=%s filename=%s mime=%s size=%d", media_id, file.filename, mime_type, len(data))
    return {"media_id": media_id, "filename": file.filename, "mime_type": mime_type, "size": len(data)}


# ── POST /api/stt ─────────────────────────────────────────────────────────────

@router.post("/api/stt")
async def speech_to_text(file: UploadFile, _: str = Depends(_require_bearer)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio too large (max 25 MB)")

    ct = file.content_type or ""
    if "mp4" in ct or "m4a" in ct:
        suffix = ".mp4"
    elif "ogg" in ct:
        suffix = ".ogg"
    elif "wav" in ct:
        suffix = ".wav"
    else:
        suffix = ".webm"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        tmp_path = f.name

    try:
        loop = asyncio.get_running_loop()
        model = _get_whisper()
        segments, _ = await loop.run_in_executor(
            None, lambda: model.transcribe(tmp_path, beam_size=5)
        )
        text = " ".join(s.text for s in segments).strip()
        logger.info("STT transcribed %d bytes → %d chars", len(data), len(text))
        return {"text": text}
    except Exception as exc:
        logger.exception("STT failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)


# ── GET /api/tts ──────────────────────────────────────────────────────────────

# Google Neural2 voices keyed by (lang, gender).
# Gender is inferred from the stored edge-tts voice name.
_GOOGLE_VOICES: dict[tuple[str, str], tuple[str, str]] = {
    ("he", "male"):   ("he-IL", "he-IL-Neural2-B"),
    ("he", "female"): ("he-IL", "he-IL-Neural2-A"),
    ("ar", "male"):   ("ar-XA", "ar-XA-Neural2-B"),
    ("ar", "female"): ("ar-XA", "ar-XA-Neural2-A"),
    ("en", "male"):   ("en-US", "en-US-Neural2-D"),
    ("en", "female"): ("en-US", "en-US-Neural2-F"),
}
# edge-tts voice name fragments that indicate male gender
_MALE_INDICATORS = {"Avri", "Guy", "Hamed"}


def _rate_to_speaking_rate(rate: str) -> float:
    """Convert edge-tts rate string (e.g. '+25%') to Google speakingRate float."""
    try:
        return max(0.25, min(4.0, 1.0 + int(rate.replace("%", "").replace("+", "")) / 100))
    except Exception:
        return 1.0


@router.get("/api/tts")
async def text_to_speech(text: str = Query(...), _: str = Depends(_require_bearer)):
    text = _clean_for_tts(text)
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")

    from app.tts_config import get_tts_config
    from app.config import GOOGLE_TTS_API_KEY
    cfg = get_tts_config()
    edge_voices = cfg["voices"]
    rate = cfg["rate"]

    is_he = bool(re.search(r"[֐-׿]", text))
    is_ar = bool(re.search(r"[؀-ۿ]", text))
    lang = "he" if is_he else "ar" if is_ar else "en"
    edge_voice = edge_voices[lang]
    gender = "male" if any(m in edge_voice for m in _MALE_INDICATORS) else "female"

    # ── Primary: Google Cloud TTS Neural2 ────────────────────────────────────
    if GOOGLE_TTS_API_KEY:
        lang_code, google_voice = _GOOGLE_VOICES[(lang, gender)]
        try:
            import httpx, base64 as _b64
            payload = {
                "input": {"text": text},
                "voice": {"languageCode": lang_code, "name": google_voice},
                "audioConfig": {
                    "audioEncoding": "MP3",
                    "speakingRate": _rate_to_speaking_rate(rate),
                },
            }
            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.post(
                    "https://texttospeech.googleapis.com/v1/text:synthesize",
                    params={"key": GOOGLE_TTS_API_KEY},
                    json=payload,
                )
            if resp.status_code == 200:
                audio_data = _b64.b64decode(resp.json()["audioContent"])
                logger.info("Google TTS: %d chars → %d bytes voice=%s rate=%s",
                            len(text), len(audio_data), google_voice, rate)
                return Response(content=audio_data, media_type="audio/mpeg")
            logger.warning("Google TTS %d — falling back to edge-tts: %s",
                           resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Google TTS error (%s) — falling back to edge-tts", exc)

    # ── Fallback: edge-tts ────────────────────────────────────────────────────
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, edge_voice, rate=rate)
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        audio_data = b"".join(chunks)
        logger.info("edge-tts: %d chars → %d bytes voice=%s rate=%s",
                    len(text), len(audio_data), edge_voice, rate)
        return Response(content=audio_data, media_type="audio/mpeg")
    except Exception as exc:
        logger.exception("TTS failed (Google + edge-tts both failed)")
        raise HTTPException(status_code=500, detail=str(exc))

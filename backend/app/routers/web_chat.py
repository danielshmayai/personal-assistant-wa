"""
Web chat router — WebSocket streaming chat, conversations, memory, and file upload.

Endpoints:
  WS  /ws/chat              — streaming chat  (?token=&chat_id=)
  GET /api/conversations    — list web conversations
  GET /api/memory           — facts and rules for the sidebar
  POST /api/upload          — upload a file into media_cache
"""

import contextlib
import json
import logging
import mimetypes
import os
import tempfile
import uuid
import asyncio

from fastapi import APIRouter, Depends, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import TEST_TOKEN

logger = logging.getLogger("pa.web_chat")
router = APIRouter()

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_AUDIO_BYTES  = 25 * 1024 * 1024  # 25 MB

# Lazy-loaded Whisper model (loaded on first STT request)
_whisper = None

def _get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel("tiny", device="cpu", compute_type="int8")
        logger.info("Whisper tiny model loaded")
    return _whisper


# ── Auth ─────────────────────────────────────────────────────────────────────

def _verify_token(token: str) -> bool:
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
                    full_text = f"{media_tag}\n{user_text}" if user_text else f"{media_tag}\nPlease save this to Google Drive."
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
                logger.exception("stream_graph failed for chat_id=%s", chat_id)
                await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))

    except WebSocketDisconnect:
        logger.info("Web chat disconnected: chat_id=%s", chat_id)


# ── GET /api/conversations ────────────────────────────────────────────────────

@router.get("/api/conversations")
async def get_conversations(_: str = Depends(_require_bearer)):
    from app.memory.store import list_web_conversations
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, list_web_conversations)


# ── GET /api/memory ───────────────────────────────────────────────────────────

@router.get("/api/memory")
async def get_memory(_: str = Depends(_require_bearer)):
    from app.memory.store import get_all_facts_with_ids, get_all_rules_with_ids
    loop = asyncio.get_running_loop()
    facts = await loop.run_in_executor(None, get_all_facts_with_ids)
    rules = await loop.run_in_executor(None, get_all_rules_with_ids)
    return {"facts": facts, "rules": rules}


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

"""
Web chat router — WebSocket streaming chat, memory API, and file upload.

Endpoints:
  WS  /ws/chat          — streaming chat (auth: ?token= query param)
  GET /api/memory       — facts and rules for the sidebar (Bearer token)
  POST /api/upload      — upload a file into media_cache (Bearer token)
"""

import json
import logging
import mimetypes
import uuid
import asyncio

from fastapi import APIRouter, Depends, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import TEST_TOKEN

logger = logging.getLogger("pa.web_chat")
router = APIRouter()

_WEB_CHAT_ID = "web"
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


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
async def websocket_chat(websocket: WebSocket, token: str = ""):
    if not _verify_token(token):
        await websocket.close(code=4403, reason="Unauthorized")
        return

    await websocket.accept()
    logger.info("Web chat WebSocket connected")

    # Send conversation history on connect
    try:
        from app.graph.streaming import get_history
        history = await get_history(_WEB_CHAT_ID)
        await websocket.send_text(json.dumps({"type": "history", "messages": history}))
    except Exception:
        logger.exception("Failed to send history on WebSocket connect")

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

            # Stream response events
            try:
                from app.graph.streaming import stream_graph
                async for event in stream_graph(full_text, _WEB_CHAT_ID):
                    await websocket.send_text(json.dumps(event))
            except Exception as exc:
                logger.exception("stream_graph failed for web chat")
                await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))

    except WebSocketDisconnect:
        logger.info("Web chat WebSocket disconnected")


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

"""Product chat WebSocket + upload: cookie-authenticated, tenant-scoped streaming.

Event protocol to the client mirrors the engine's stream_graph events
(thinking_token / token / tool_start / tool_end / done) plus structured
error events: {"type": "error", "code": "invalid_key" | "quota_exceeded" |
"rate_limited" | "internal"} — never raw stack traces.
"""
import logging
import mimetypes
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, WebSocket, WebSocketDisconnect

from app.context import current_tenant_id, request_id_var
from product.auth.sessions import COOKIE_NAME
from product.deps import check_chat_rate, require_session, resolve_session_tenant
from product.modules.store import get_enabled_modules
from product.tenancy.models import Tenant

logger = logging.getLogger("product.chat")

router = APIRouter()

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB — chat attachments, not video


@router.post("/api/upload")
async def upload_file(file: UploadFile, _: Tenant = Depends(require_session)):
    """Cache a chat attachment into media_cache under the caller's tenant scope.

    Scoping is automatic: require_session sets current_tenant_id before this
    runs, and media_cache.store_web_upload stamps the entry with that scope —
    the same isolation nutrition/fitness image uploads already rely on.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty_file")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file_too_large")

    mime_type = file.content_type or ""
    if not mime_type or mime_type == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(file.filename or "")
        mime_type = guessed or "application/octet-stream"

    from app.media_cache import store_web_upload
    media_id = f"web_{uuid.uuid4().hex}"
    store_web_upload(media_id, data, mime_type, file.filename or media_id)
    return {"media_id": media_id, "filename": file.filename, "mime_type": mime_type, "size": len(data)}


def _classify_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()
    if "api key" in text or "api_key" in text or "401" in text or "permission" in text or "invalid" in text and "key" in text:
        return "invalid_key"
    if "quota" in text or "429" in text or "resource exhausted" in text or "rate" in text:
        return "quota_exceeded"
    return "internal"


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    tenant = resolve_session_tenant(websocket.cookies.get(COOKIE_NAME))
    if tenant is None:
        await websocket.close(code=4401, reason="not_authenticated")
        return
    if tenant.onboarded_at is None:
        await websocket.close(code=4403, reason="onboarding_required")
        return

    await websocket.accept()

    scope = tenant.engine_scope
    # Conversation thread id embeds the engine scope → checkpoints isolate
    # per tenant, and offboarding can delete threads by prefix.
    chat_id = f"web_{scope}_{uuid.uuid4().hex[:12]}"

    # Stable per-tenant key (not the ephemeral thread id above) — matches
    # scheduled_jobs._scoped_chat_id / push.py's _push_chat_id, so a
    # scheduled reminder fired from the tenant scheduler can reach this
    # session via NotificationManager's live WS broadcast.
    notify_key = f"web_{scope}" if scope else "web"
    from app.broadcast import NotificationManager
    NotificationManager.register(notify_key, websocket)

    from app.graph.graph import stream_graph  # deferred: heavy import

    try:
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") != "message":
                continue
            text = (payload.get("text") or "").strip()
            media_id = payload.get("media_id")
            if not text and not media_id:
                continue
            if isinstance(payload.get("chat_id"), str) and payload["chat_id"].startswith(f"web_{scope}_"):
                chat_id = payload["chat_id"]  # continue an existing thread — own prefix only

            if not check_chat_rate(tenant.id):
                await websocket.send_json({"type": "error", "code": "rate_limited"})
                continue

            # Set before retrieve() below: media_cache scope-checks against
            # current_tenant_id, so it must be set before we touch the cache.
            current_tenant_id.set(scope)
            request_id_var.set(str(uuid.uuid4()))
            await websocket.send_json({"type": "ack", "chat_id": chat_id})

            full_text = text
            if media_id:
                from app.media_cache import retrieve
                cached = retrieve(media_id)
                if cached:
                    mime = cached.get("mime_type", "application/octet-stream")
                    fname = cached.get("filename", f"upload_{media_id[:8]}")
                    broad = "image" if mime.startswith("image/") else "document"
                    media_tag = f"[MEDIA id={media_id} type={broad} filename={fname} mime={mime}]"
                    full_text = f"{media_tag}\n{text}" if text else (
                        f"{media_tag}\n(no caption — follow the media routing rules: "
                        "image → analyze first, document → save)"
                    )
                elif not text:
                    full_text = "Please save my file to Google Drive."

            try:
                async for event in stream_graph(
                    full_text, chat_id,
                    enabled_modules=get_enabled_modules(tenant.id, tenant.is_owner),
                ):
                    await websocket.send_json(event)
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                code = _classify_error(exc)
                logger.error(
                    "chat stream failed code=%s chat_id=%s: %s", code, chat_id, exc,
                    extra={"tenant_id": tenant.id, "chat_id": chat_id, "error_code": code},
                )
                await websocket.send_json({"type": "error", "code": code})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("chat websocket closed unexpectedly", extra={"tenant_id": tenant.id})
    finally:
        NotificationManager.unregister(notify_key)

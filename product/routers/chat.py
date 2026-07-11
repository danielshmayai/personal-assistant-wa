"""Product chat WebSocket: cookie-authenticated, tenant-scoped streaming.

Event protocol to the client mirrors the engine's stream_graph events
(thinking_token / token / tool_start / tool_end / done) plus structured
error events: {"type": "error", "code": "invalid_key" | "quota_exceeded" |
"rate_limited" | "internal"} — never raw stack traces.
"""
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.context import current_tenant_id, request_id_var
from product.auth.sessions import COOKIE_NAME
from product.deps import check_chat_rate, resolve_session_tenant
from product.modules.store import get_enabled_modules

logger = logging.getLogger("product.chat")

router = APIRouter()


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

    from app.graph.graph import stream_graph  # deferred: heavy import

    try:
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") != "message":
                continue
            text = (payload.get("text") or "").strip()
            if not text:
                continue
            if isinstance(payload.get("chat_id"), str) and payload["chat_id"].startswith(f"web_{scope}_"):
                chat_id = payload["chat_id"]  # continue an existing thread — own prefix only

            if not check_chat_rate(tenant.id):
                await websocket.send_json({"type": "error", "code": "rate_limited"})
                continue

            current_tenant_id.set(scope)
            request_id_var.set(str(uuid.uuid4()))
            await websocket.send_json({"type": "ack", "chat_id": chat_id})

            try:
                async for event in stream_graph(
                    text, chat_id,
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

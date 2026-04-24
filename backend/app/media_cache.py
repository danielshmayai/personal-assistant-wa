"""
In-memory cache for WhatsApp media bytes received via WAHA webhooks.

WAHA WEBJS embeds media as base64 in `_data.body` inside the webhook payload
itself — no separate download API call is required. This module caches those
bytes (keyed by message_id) so Drive tools can retrieve them without hitting
WAHA again.

Falls back to storing the `mediaUrl` for later HTTP download when the
base64 body is absent (can happen for very large files or WAHA Plus setups).
"""

import base64
import logging
from collections import OrderedDict

logger = logging.getLogger("pa.media_cache")

# LRU-style eviction: oldest entry is dropped when cache is full
_cache: "OrderedDict[str, dict]" = OrderedDict()
_MAX_ENTRIES = 50  # ~50 photos × ~3 MB each ≈ 150 MB worst-case


def store_from_payload(message_id: str, payload: dict) -> bool:
    """
    Try to cache media from a WAHA webhook payload.

    Priority:
      1. _data.body  — base64 blob embedded by WEBJS engine (most common)
      2. mediaUrl    — URL served by WAHA (store reference for lazy download)

    Returns True if something was cached.
    """
    if not message_id:
        return False

    data_field = payload.get("_data", {})
    mime_type = (
        data_field.get("mimetype", "")
        or "application/octet-stream"
    )

    # 1. Try embedded base64 ──────────────────────────────────────────────────
    b64 = data_field.get("body", "")
    if b64 and len(b64) > 100:
        try:
            data = base64.b64decode(b64)
            _evict()
            _cache[message_id] = {"data": data, "mime_type": mime_type}
            logger.debug(
                "media_cache: cached %d bytes (base64) for msg %s",
                len(data),
                message_id[-12:],
            )
            return True
        except Exception as exc:
            logger.debug(
                "media_cache: b64 decode failed for %s: %s",
                message_id[-12:],
                exc,
            )

    # 2. Fall back to mediaUrl reference ─────────────────────────────────────
    media_url = payload.get("mediaUrl", "")
    if media_url:
        _evict()
        _cache[message_id] = {"media_url": media_url, "mime_type": mime_type}
        logger.debug(
            "media_cache: cached mediaUrl for msg %s", message_id[-12:]
        )
        return True

    return False


def retrieve(message_id: str) -> dict | None:
    """Return the cached entry dict (keys: 'data'|'media_url', 'mime_type') or None."""
    return _cache.get(message_id)


def store_web_upload(media_id: str, data: bytes, mime_type: str, filename: str) -> None:
    """Cache bytes uploaded via the web UI under a synthetic media_id."""
    _evict()
    _cache[media_id] = {"data": data, "mime_type": mime_type, "filename": filename}
    logger.debug("media_cache: web upload cached %d bytes for media_id=%s", len(data), media_id)


def _evict() -> None:
    while len(_cache) >= _MAX_ENTRIES:
        _cache.popitem(last=False)

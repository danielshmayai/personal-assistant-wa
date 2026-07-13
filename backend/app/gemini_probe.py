"""Shared logic: which Gemini model a given API key can actually run.

Free AI Studio keys can't always run the strongest model. Both the engine's
self-healing LLM factory (llm.py, for keys saved before this existed) and the
product secrets/onboarding routes (for keys being saved right now) probe with
the same candidate order and classification so they never diverge.
"""
import logging

from app.config import GEMINI_MODEL

logger = logging.getLogger("pa.gemini_probe")

# Order matters: strongest first.
FALLBACK_MODELS = ["gemini-2.5-flash-lite", "gemini-2.0-flash"]

GENLANG_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

_PROBE_BODY = {
    "contents": [{"parts": [{"text": "hi"}]}],
    "generationConfig": {"maxOutputTokens": 1},
}


def _candidates(preferred: str | None) -> list[str]:
    preferred = preferred or GEMINI_MODEL
    return [preferred] + [m for m in FALLBACK_MODELS if m != preferred]


def _classify_response(model: str, preferred: str, status_code: int, body: dict) -> dict | None:
    """Decisive result for this response, or None to try the next candidate."""
    if status_code == 200:
        return {"ok": True, "reason": "ok", "model": model, "limited": model != preferred}
    if status_code == 429:
        # Key and model are fine — just out of free-tier quota right now.
        return {"ok": True, "reason": "quota_warning", "model": model, "limited": True}
    detail = ""
    try:
        err = body.get("error", {})
        detail = f'{err.get("status", "")} {err.get("message", "")}'.lower()
    except Exception:
        pass
    if "api key not valid" in detail or "api_key_invalid" in detail or status_code == 401:
        return {"ok": False, "reason": "invalid_key", "model": None, "limited": False}
    return None  # model not available for this key/tier/region — try the next one


def resolve_gemini_access_sync(api_key: str, preferred: str | None = None) -> dict:
    """Blocking probe for use inside the sync engine LLM factory (llm.py).

    Only called once per tenant per process lifetime (the caller caches the
    result), so the blocking network call is a rare, bounded cost — not a
    per-message one.
    """
    import httpx

    preferred = preferred or GEMINI_MODEL
    try:
        with httpx.Client(timeout=15) as client:
            for model in _candidates(preferred):
                try:
                    resp = client.post(
                        f"{GENLANG_BASE}/{model}:generateContent",
                        params={"key": api_key}, json=_PROBE_BODY,
                    )
                except Exception:
                    return {"ok": True, "reason": "unverified_network", "model": preferred, "limited": False}
                try:
                    body = resp.json()
                except Exception:
                    body = {}
                result = _classify_response(model, preferred, resp.status_code, body)
                if result:
                    return result
    except Exception:
        logger.debug("resolve_gemini_access_sync failed", exc_info=True)
        return {"ok": True, "reason": "unverified_network", "model": preferred, "limited": False}
    return {"ok": False, "reason": "no_supported_model", "model": None, "limited": False}


async def resolve_gemini_access(api_key: str, preferred: str | None = None) -> dict:
    """Async probe for the product onboarding/settings endpoints."""
    import httpx

    preferred = preferred or GEMINI_MODEL
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for model in _candidates(preferred):
                try:
                    resp = await client.post(
                        f"{GENLANG_BASE}/{model}:generateContent",
                        params={"key": api_key}, json=_PROBE_BODY,
                    )
                except Exception:
                    return {"ok": True, "reason": "unverified_network", "model": preferred, "limited": False}
                try:
                    body = resp.json()
                except Exception:
                    body = {}
                result = _classify_response(model, preferred, resp.status_code, body)
                if result:
                    return result
    except Exception:
        logger.debug("resolve_gemini_access failed", exc_info=True)
        return {"ok": True, "reason": "unverified_network", "model": preferred, "limited": False}
    return {"ok": False, "reason": "no_supported_model", "model": None, "limited": False}

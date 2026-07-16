"""Shared logic: which Gemini model a given API key can actually run.

Product policy: the assistant runs on Gemini 2.5 Flash and below by default
— it never automatically escalates to a stronger/costlier model (e.g. the
"pro" tier). A tenant can explicitly opt into stronger models in Settings;
until then, only STANDARD_MODELS are ever tried.

Both the engine's self-healing LLM factory (llm.py, for keys saved before
this existed) and the product secrets/onboarding routes (for keys being
saved right now) probe with the same candidate order and classification so
they never diverge.
"""
import logging

logger = logging.getLogger("pa.gemini_probe")

# Tried in order, strongest first. Never exceeded unless a tenant opts in
# (see PREMIUM_MODELS) — this is the hard ceiling for automatic selection.
STANDARD_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]

# Opt-in only. Tried first, ahead of STANDARD_MODELS, but only when the
# caller explicitly passes allow_premium=True (a tenant setting toggled in
# Settings) — never reached by default.
PREMIUM_MODELS = ["gemini-2.5-pro"]

GENLANG_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

_PROBE_BODY = {
    "contents": [{"parts": [{"text": "hi"}]}],
    "generationConfig": {"maxOutputTokens": 1},
}


def _candidates(allow_premium: bool) -> list[str]:
    return (PREMIUM_MODELS + STANDARD_MODELS) if allow_premium else list(STANDARD_MODELS)


def _classify_response(model: str, status_code: int, body: dict) -> dict | None:
    """Decisive result for this response, or None to try the next candidate."""
    if status_code == 200:
        # "limited" flags a step DOWN from the top standard model (free-tier
        # constraint) — never true for the top standard model itself, and
        # never true for an opted-in premium model (that's an upgrade).
        limited = model in STANDARD_MODELS[1:]
        return {"ok": True, "reason": "ok", "model": model, "limited": limited}
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


def resolve_gemini_access_sync(api_key: str, allow_premium: bool = False) -> dict:
    """Blocking probe for use inside the sync engine LLM factory (llm.py).

    Only called once per tenant per process lifetime (the caller caches the
    result), so the blocking network call is a rare, bounded cost — not a
    per-message one.
    """
    import httpx

    try:
        with httpx.Client(timeout=15) as client:
            for model in _candidates(allow_premium):
                try:
                    resp = client.post(
                        f"{GENLANG_BASE}/{model}:generateContent",
                        params={"key": api_key}, json=_PROBE_BODY,
                    )
                except Exception:
                    return {"ok": True, "reason": "unverified_network", "model": STANDARD_MODELS[0], "limited": False}
                try:
                    body = resp.json()
                except Exception:
                    body = {}
                result = _classify_response(model, resp.status_code, body)
                if result:
                    return result
    except Exception:
        logger.debug("resolve_gemini_access_sync failed", exc_info=True)
        return {"ok": True, "reason": "unverified_network", "model": STANDARD_MODELS[0], "limited": False}
    return {"ok": False, "reason": "no_supported_model", "model": None, "limited": False}


async def resolve_gemini_access(api_key: str, allow_premium: bool = False) -> dict:
    """Async probe for the product onboarding/settings endpoints."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for model in _candidates(allow_premium):
                try:
                    resp = await client.post(
                        f"{GENLANG_BASE}/{model}:generateContent",
                        params={"key": api_key}, json=_PROBE_BODY,
                    )
                except Exception:
                    return {"ok": True, "reason": "unverified_network", "model": STANDARD_MODELS[0], "limited": False}
                try:
                    body = resp.json()
                except Exception:
                    body = {}
                result = _classify_response(model, resp.status_code, body)
                if result:
                    return result
    except Exception:
        logger.debug("resolve_gemini_access failed", exc_info=True)
        return {"ok": True, "reason": "unverified_network", "model": STANDARD_MODELS[0], "limited": False}
    return {"ok": False, "reason": "no_supported_model", "model": None, "limited": False}

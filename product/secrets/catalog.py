"""Catalog of tenant-suppliable secrets (BYO keys) shown in the config UI.

Platform secrets (GOOGLE_CLIENT_ID/SECRET, SESSION_SECRET, SECRETS_MASTER_KEY,
DATABASE_URL) are deliberately NOT here — tenants never see or set them.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretSpec:
    key: str
    label: str
    module: str          # module that needs it ("chat" = core)
    required: bool = False
    help_url: str = ""
    placeholder: str = ""


SECRET_SPECS: list[SecretSpec] = [
    SecretSpec(
        key="GEMINI_API_KEY", label="Google Gemini API Key", module="chat",
        required=True, help_url="https://aistudio.google.com/apikey",
        placeholder="AIza...",
    ),
    SecretSpec(
        key="TAVILY_API_KEY", label="Tavily Search API Key", module="web-search",
        help_url="https://app.tavily.com", placeholder="tvly-...",
    ),
    SecretSpec(key="TUYA_ACCESS_ID", label="Tuya Access ID", module="smart-home"),
    SecretSpec(key="TUYA_ACCESS_KEY", label="Tuya Access Key", module="smart-home"),
    SecretSpec(
        key="TUYA_API_ENDPOINT", label="Tuya API Endpoint", module="smart-home",
        placeholder="https://openapi.tuyaeu.com",
    ),
    SecretSpec(
        key="GOOGLE_TTS_API_KEY", label="Google Cloud TTS API Key", module="tts",
    ),
    SecretSpec(key="GARMIN_EMAIL", label="Garmin Connect Email", module="fitness"),
    SecretSpec(key="GARMIN_PASSWORD", label="Garmin Connect Password", module="fitness"),
]

ALLOWED_KEYS = {s.key for s in SECRET_SPECS}


def mask_hint(value: str) -> str:
    """Safe display hint: only the last 4 characters."""
    return f"…{value[-4:]}" if len(value) >= 8 else "•••"


async def validate_secret(key: str, value: str) -> tuple[bool, str]:
    """Cheap live validation where possible. Returns (ok, message)."""
    if key == "GEMINI_API_KEY":
        result = await resolve_gemini_access(value)
        return result["ok"], result["reason"]
    return True, "unvalidated"


# Free AI Studio keys can't always run the preferred model — probe downwards
# until one answers. Order matters: strongest first.
GEMINI_FALLBACK_MODELS = ["gemini-2.5-flash-lite", "gemini-2.0-flash"]

_GENLANG_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _preferred_model() -> str:
    from app.config import GEMINI_MODEL
    return GEMINI_MODEL


async def resolve_gemini_access(api_key: str) -> dict:
    """Probe which Gemini model this key can actually run.

    Prefers the platform's strong model; falls back down GEMINI_FALLBACK_MODELS
    for free-tier keys. A 429 means the key and model are fine but currently
    throttled — usable, flagged limited. Only a hard key rejection fails.

    Returns {"ok", "reason", "model", "limited"}.
    """
    import httpx

    preferred = _preferred_model()
    candidates = [preferred] + [m for m in GEMINI_FALLBACK_MODELS if m != preferred]
    probe_body = {
        "contents": [{"parts": [{"text": "hi"}]}],
        "generationConfig": {"maxOutputTokens": 1},
    }

    network_failed = False
    async with httpx.AsyncClient(timeout=15) as client:
        for model in candidates:
            try:
                resp = await client.post(
                    f"{_GENLANG_BASE}/{model}:generateContent",
                    params={"key": api_key},
                    json=probe_body,
                )
            except Exception:
                network_failed = True
                break

            if resp.status_code == 200:
                return {"ok": True, "reason": "ok", "model": model,
                        "limited": model != preferred}
            if resp.status_code == 429:
                # Key valid, model supported — just out of free-tier quota right
                # now. Accept it; the UI warns about free-tier limits.
                return {"ok": True, "reason": "quota_warning", "model": model,
                        "limited": True}

            # Hard key rejection ends the probe; anything else (model not
            # available to this key/tier/region) tries the next candidate.
            detail = ""
            try:
                err = resp.json().get("error", {})
                detail = f'{err.get("status", "")} {err.get("message", "")}'.lower()
            except Exception:
                pass
            if "api key not valid" in detail or "api_key_invalid" in detail or resp.status_code == 401:
                return {"ok": False, "reason": "invalid_key", "model": None, "limited": False}

    if network_failed:
        # Our own network hiccup must not block the user from saving a key.
        return {"ok": True, "reason": "unverified_network", "model": preferred, "limited": False}
    return {"ok": False, "reason": "no_supported_model", "model": None, "limited": False}

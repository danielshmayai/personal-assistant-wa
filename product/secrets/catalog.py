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
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": value, "pageSize": 1},
                )
            if resp.status_code == 200:
                return True, "ok"
            if resp.status_code in (400, 401, 403):
                return False, "invalid_key"
            if resp.status_code == 429:
                return True, "quota_warning"  # key is real, currently throttled
            return True, f"unverified_http_{resp.status_code}"
        except Exception:
            return True, "unverified_network"  # don't block saving on our own network issues
    return True, "unvalidated"

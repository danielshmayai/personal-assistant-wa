"""Catalog of tenant-suppliable secrets (BYO keys) shown in the config UI.

Platform secrets (GOOGLE_CLIENT_ID/SECRET, SESSION_SECRET, SECRETS_MASTER_KEY,
DATABASE_URL) are deliberately NOT here — tenants never see or set them.
"""
from dataclasses import dataclass

# Re-exported so existing importers (product/routers/secrets.py,
# product/routers/onboarding.py) don't need to change. The probe logic lives
# in the engine (app.gemini_probe) so llm.py's self-heal path and this
# save-time validation always agree on which models are viable.
from app.gemini_probe import FALLBACK_MODELS as GEMINI_FALLBACK_MODELS  # noqa: F401
from app.gemini_probe import resolve_gemini_access  # noqa: F401


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
        placeholder="AIza... or AQ....",
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

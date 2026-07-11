"""Product module catalog: what can be toggled per tenant.

module ids map to engine tool groups via
app/graph/tools_registry._MODULE_GROUPS — keep the two in sync.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Module:
    id: str
    label: str
    description: str
    core: bool = False           # cannot be disabled
    default_on: bool = False
    owner_only: bool = False     # locked for non-owner tenants (skeleton)
    needs_oauth: bool = False    # requires the Google connect flow
    secret_keys: tuple[str, ...] = field(default_factory=tuple)


MODULES: list[Module] = [
    Module("chat", "Chat", "Conversational assistant with vision", core=True, default_on=True,
           secret_keys=("GEMINI_API_KEY",)),
    Module("memory", "Memory", "Long-term memory: facts, rules, notes vault", default_on=True),
    Module("web-search", "Web Search", "Web, Wikipedia and weather lookups", default_on=True,
           secret_keys=("TAVILY_API_KEY",)),
    Module("google", "Google Services", "Gmail, Calendar, Drive and Maps", needs_oauth=True),
    Module("nutrition", "Nutrition", "Meal and water tracking with macro analysis"),
    Module("fitness", "Fitness", "Workout logging, progressive overload, body metrics",
           secret_keys=("GARMIN_EMAIL", "GARMIN_PASSWORD")),
    Module("smart-home", "Smart Home", "Tuya device control",
           secret_keys=("TUYA_ACCESS_ID", "TUYA_ACCESS_KEY")),
    # The scheduled-jobs executor is still owner-global and delivers over
    # WhatsApp only, so web tenants can't receive results yet.
    Module("scheduling", "Scheduling", "Reminders and scheduled actions (coming soon for web accounts)",
           owner_only=True),
    Module("tts", "Text-to-Speech", "Voice replies", secret_keys=("GOOGLE_TTS_API_KEY",)),
]

MODULES_BY_ID = {m.id: m for m in MODULES}


def default_enabled_ids() -> list[str]:
    return [m.id for m in MODULES if m.default_on or m.core]


def allowed_ids_for(is_owner: bool) -> set[str]:
    return {m.id for m in MODULES if is_owner or not m.owner_only}

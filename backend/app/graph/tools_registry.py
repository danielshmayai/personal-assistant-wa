from __future__ import annotations

# Cache key: (tenant_id, chat_id, sorted-modules-or-None). A plain chat_id
# key would leak one tenant's bound tools (and OAuth state) into another.
_tool_cache: dict[tuple, list] = {}

# Maps a product module id → the tool groups it unlocks. None modules
# (legacy owner path) means every group is enabled.
_MODULE_GROUPS: dict[str, tuple[str, ...]] = {
    "chat": ("image",),          # core: vision/analyze tools ride along with chat
    "web-search": ("web",),
    "google": ("google",),
    "smart-home": ("tuya",),
    "memory": ("memory",),
    "scheduling": ("schedule",),
    "nutrition": ("nutrition",),
    "fitness": ("fitness",),
    "tts": ("tts",),
}


def _cache_key(chat_id: str, tenant_id: str, modules: list[str] | None) -> tuple:
    return (tenant_id, chat_id, tuple(sorted(modules)) if modules is not None else None)


def get_all_tools(chat_id: str, tenant_id: str = "", modules: list[str] | None = None) -> list:
    """Return the cached tool list for this (tenant, chat, modules) combo."""
    key = _cache_key(chat_id, tenant_id, modules)
    if key not in _tool_cache:
        _tool_cache[key] = _build(chat_id, modules)
    return _tool_cache[key]


def clear_tools_cache(chat_id: str | None = None) -> None:
    """Bust the tool cache. Call after Google OAuth completes for a user."""
    if chat_id:
        for key in [k for k in _tool_cache if k[1] == chat_id]:
            _tool_cache.pop(key, None)
    else:
        _tool_cache.clear()


def clear_tools_cache_for_tenant(tenant_id: str) -> None:
    """Bust every cached tool list belonging to one tenant (secret rotation)."""
    for key in [k for k in _tool_cache if k[0] == tenant_id]:
        _tool_cache.pop(key, None)


def _enabled_groups(modules: list[str] | None) -> set[str] | None:
    if modules is None:
        return None  # legacy owner path — everything on
    groups: set[str] = set()
    for m in modules:
        groups.update(_MODULE_GROUPS.get(m, ()))
    groups.update(_MODULE_GROUPS["chat"])  # chat core is always on
    return groups


def _build(chat_id: str, modules: list[str] | None = None) -> list:
    from app.google.tools import get_google_tools
    from app.tuya.tools import get_tuya_tools
    from app.memory.manager import MEMORY_TOOLS
    from app.web.tools import WEB_TOOLS
    from app.tts_tool import TTS_TOOLS
    from app.schedule_tool import get_schedule_tools
    from app.nutrition_tool import get_nutrition_tools
    from app.fitness_tool import get_fitness_tools
    from app.image_tools import get_image_tools

    groups = _enabled_groups(modules)

    def on(group: str) -> bool:
        return groups is None or group in groups

    tools: list = []
    if on("web"):
        tools += WEB_TOOLS
    if on("google"):
        tools += get_google_tools(chat_id)
    if on("tuya"):
        tools += get_tuya_tools()
    if on("memory"):
        tools += MEMORY_TOOLS
    if on("tts"):
        tools += TTS_TOOLS
    if on("schedule"):
        tools += get_schedule_tools(chat_id)
    if on("nutrition"):
        tools += get_nutrition_tools(chat_id)
    if on("fitness"):
        tools += get_fitness_tools(chat_id)
    if on("image"):
        tools += get_image_tools(chat_id)
    return tools

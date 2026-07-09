from __future__ import annotations

_tool_cache: dict[str, list] = {}


def get_all_tools(chat_id: str) -> list:
    """Return the cached tool list for chat_id, building it on first access."""
    if chat_id not in _tool_cache:
        _tool_cache[chat_id] = _build(chat_id)
    return _tool_cache[chat_id]


def clear_tools_cache(chat_id: str | None = None) -> None:
    """Bust the tool cache. Call after Google OAuth completes for a user."""
    if chat_id:
        _tool_cache.pop(chat_id, None)
    else:
        _tool_cache.clear()


def _build(chat_id: str) -> list:
    from app.google.tools import get_google_tools
    from app.tuya.tools import get_tuya_tools
    from app.memory.manager import MEMORY_TOOLS
    from app.web.tools import WEB_TOOLS
    from app.tts_tool import TTS_TOOLS
    from app.schedule_tool import get_schedule_tools
    from app.nutrition_tool import get_nutrition_tools
    from app.fitness_tool import get_fitness_tools
    from app.image_tools import get_image_tools
    return (
        WEB_TOOLS
        + get_google_tools(chat_id)
        + get_tuya_tools()
        + MEMORY_TOOLS
        + TTS_TOOLS
        + get_schedule_tools(chat_id)
        + get_nutrition_tools(chat_id)
        + get_fitness_tools(chat_id)
        + get_image_tools(chat_id)
    )

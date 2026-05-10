"""TTS configuration — persisted in the app_settings table under key 'tts_config'."""

_SETTING_KEY = "tts_config"

DEFAULTS: dict = {
    "voices": {
        "he": "he-IL-AvriNeural",
        "ar": "ar-SA-HamedNeural",
        "en": "en-US-GuyNeural",
    },
    "rate": "+25%",
}

# Full catalog of available edge-tts voices per language
VOICE_CATALOG: dict = {
    "he": {
        "male":   "he-IL-AvriNeural",
        "female": "he-IL-HilaNeural",
    },
    "ar": {
        "male":   "ar-SA-HamedNeural",
        "female": "ar-SA-ZariyahNeural",
    },
    "en": {
        "male":   "en-US-GuyNeural",
        "female": "en-US-AriaNeural",
    },
}


def get_tts_config() -> dict:
    from app.memory.store import get_app_setting
    stored = get_app_setting(_SETTING_KEY)
    if not stored:
        return DEFAULTS
    # Merge so that any new keys added to DEFAULTS are present even for old rows
    merged = {**DEFAULTS, **stored}
    merged["voices"] = {**DEFAULTS["voices"], **stored.get("voices", {})}
    return merged


def save_tts_config(config: dict) -> None:
    from app.memory.store import set_app_setting
    set_app_setting(_SETTING_KEY, config)


def reset_tts_config() -> None:
    save_tts_config(DEFAULTS)

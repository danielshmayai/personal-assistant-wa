"""LangGraph tool for the user to configure TTS voice and speed via conversation."""

import re
from langchain_core.tools import tool

from app.tts_config import DEFAULTS, VOICE_CATALOG, get_tts_config, reset_tts_config, save_tts_config

# Rate presets the agent can map natural language to
_RATE_PRESETS = {
    "slow":    "-25%",
    "normal":  "+0%",
    "default": "+0%",
    "fast":    "+25%",
    "faster":  "+40%",
    "fastest": "+60%",
}


@tool
def configure_tts(
    voice_gender: str = "",
    rate: str = "",
    reset: bool = False,
) -> str:
    """Configure the text-to-speech voice and speed. Persists across all conversations.

    Call this whenever the user asks to:
    - Change the voice (e.g. "use a female voice", "switch to male voice")
    - Adjust the speed (e.g. "speak faster", "slow down", "set speed to 50%")
    - Reset TTS to defaults (e.g. "reset voice", "back to default")

    Args:
        voice_gender: "male" or "female" — applies to all languages simultaneously.
                      Leave empty to keep the current voice.
        rate: Speech rate as a signed percentage string (e.g. "+25%", "-10%", "+0%")
              or a preset word: "slow", "normal", "fast", "faster", "fastest".
              Leave empty to keep the current rate.
        reset: If True, resets voice and rate to factory defaults and ignores other args.

    Returns:
        A short confirmation the user can read.
    """
    if reset:
        reset_tts_config()
        d = DEFAULTS
        return (
            f"TTS reset to defaults: "
            f"voice={d['voices']['en']} (male), rate={d['rate']}."
        )

    cfg = get_tts_config()
    changes = []

    # ── voice gender ──────────────────────────────────────────────────────────
    gender = voice_gender.strip().lower()
    if gender in ("male", "female"):
        new_voices = {}
        for lang, options in VOICE_CATALOG.items():
            new_voices[lang] = options.get(gender, cfg["voices"].get(lang))
        cfg["voices"] = new_voices
        changes.append(f"voice={gender}")

    # ── rate ─────────────────────────────────────────────────────────────────
    raw_rate = rate.strip().lower()
    if raw_rate:
        # Map preset words
        resolved = _RATE_PRESETS.get(raw_rate, raw_rate)
        # Normalise bare numbers like "50" → "+50%"
        if re.fullmatch(r"-?\d+", resolved):
            resolved = f"+{resolved}%" if not resolved.startswith("-") else f"{resolved}%"
        # Validate format
        if re.fullmatch(r"[+-]\d+%", resolved):
            cfg["rate"] = resolved
            changes.append(f"rate={resolved}")
        else:
            return (
                f"Couldn't parse rate '{rate}'. "
                "Use a preset (slow/normal/fast/faster/fastest) "
                "or a percentage like '+30%' or '-10%'."
            )

    if not changes:
        current = get_tts_config()
        return (
            f"No changes made. Current TTS: "
            f"voice={'male' if 'Avri' in current['voices'].get('he','') or 'Guy' in current['voices'].get('en','') else 'female'}, "
            f"rate={current['rate']}."
        )

    save_tts_config(cfg)
    return f"TTS updated — {', '.join(changes)}."


TTS_TOOLS = [configure_tts]

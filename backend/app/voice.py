"""Voice — STT (Whisper) and TTS (Google Neural2 + edge-tts fallback).

Shared between the owner's web_chat.py router and the product's tenant-scoped
voice router — extracted so both stay in sync and a fix (e.g. the
GOOGLE_TTS_API_KEY credential resolution below) only needs to happen once.

STT uses a local Whisper model — no credentials, safe to share across scopes.
TTS resolves GOOGLE_TTS_API_KEY via runtime.get_secret() per call (never a
module-level env import), so a tenant's own BYO key is used when present and
the call fails closed (falls back to the free edge-tts engine) otherwise —
same per-tenant-credential pattern as llm.py/tuya/tools.py/web/tools.py.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import tempfile

logger = logging.getLogger("pa.voice")

_MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB

_whisper = None


def _get_whisper():
    global _whisper
    if _whisper is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError(
                "faster-whisper is not installed (it is available inside the Docker container). "
                "STT is not supported in this environment."
            )
        _whisper = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _whisper


async def transcribe_audio(data: bytes, content_type: str) -> str:
    """Transcribe audio bytes to text via a local Whisper model."""
    import asyncio

    if not data:
        raise ValueError("empty_audio")
    if len(data) > _MAX_AUDIO_BYTES:
        raise ValueError("audio_too_large")

    ct = content_type or ""
    if "mp4" in ct or "m4a" in ct:
        suffix = ".mp4"
    elif "ogg" in ct:
        suffix = ".ogg"
    elif "wav" in ct:
        suffix = ".wav"
    else:
        suffix = ".webm"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        tmp_path = f.name

    try:
        loop = asyncio.get_running_loop()
        model = _get_whisper()
        segments, _ = await loop.run_in_executor(
            None, lambda: model.transcribe(tmp_path, beam_size=5)
        )
        text = " ".join(s.text for s in segments).strip()
        logger.info("STT transcribed %d bytes -> %d chars", len(data), len(text))
        return text
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)


def clean_for_tts(text: str) -> str:
    """Strip all non-speakable content so TTS reads only natural prose."""
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`\n]+`', '', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\[\[(?:[^\]|]*\|)?([^\]]*)\]\]', r'\1', text)
    text = re.sub(r'\[[^\]]{0,60}\]', '', text)
    text = re.sub(r'(?<!\w)#\w[\w-]*', '', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*{1,3}', '', text)
    text = re.sub(r'_{1,3}', '', text)
    text = re.sub(r'~~[^~]*~~', '', text)
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\|[-| :]+\|$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\|', ' ', text)
    text = re.sub(r'^[ \t]*[-•*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'<[^>]{1,80}>', '', text)
    text = re.sub(r'\(\s*https?://[^)]*\)', '', text)
    text = re.sub(r'[`~^]', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# Google Neural2 voices keyed by (lang, gender). Gender is inferred from the
# stored edge-tts voice name.
_GOOGLE_VOICES: dict[tuple[str, str], tuple[str, str]] = {
    ("he", "male"):   ("he-IL", "he-IL-Neural2-B"),
    ("he", "female"): ("he-IL", "he-IL-Neural2-A"),
    ("ar", "male"):   ("ar-XA", "ar-XA-Neural2-B"),
    ("ar", "female"): ("ar-XA", "ar-XA-Neural2-A"),
    ("en", "male"):   ("en-US", "en-US-Neural2-D"),
    ("en", "female"): ("en-US", "en-US-Neural2-F"),
}
_MALE_INDICATORS = {"Avri", "Guy", "Hamed"}


def _rate_to_speaking_rate(rate: str) -> float:
    try:
        return max(0.25, min(4.0, 1.0 + int(rate.replace("%", "").replace("+", "")) / 100))
    except Exception:
        return 1.0


async def synthesize_speech(text: str) -> bytes:
    """Text -> MP3 bytes. Google Neural2 if a key resolves for this scope, else edge-tts."""
    from app import runtime
    from app.tts_config import get_tts_config

    text = clean_for_tts(text)
    if not text:
        raise ValueError("empty_text")

    cfg = get_tts_config()
    edge_voices = cfg["voices"]
    rate = cfg["rate"]

    is_he = bool(re.search(r"[֐-׿]", text))
    is_ar = bool(re.search(r"[؀-ۿ]", text))
    lang = "he" if is_he else "ar" if is_ar else "en"
    edge_voice = edge_voices[lang]
    gender = "male" if any(m in edge_voice for m in _MALE_INDICATORS) else "female"

    google_key = runtime.get_secret("GOOGLE_TTS_API_KEY")
    if google_key:
        lang_code, google_voice = _GOOGLE_VOICES[(lang, gender)]
        try:
            import httpx
            import base64 as _b64
            payload = {
                "input": {"text": text},
                "voice": {"languageCode": lang_code, "name": google_voice},
                "audioConfig": {
                    "audioEncoding": "MP3",
                    "speakingRate": _rate_to_speaking_rate(rate),
                },
            }
            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.post(
                    "https://texttospeech.googleapis.com/v1/text:synthesize",
                    params={"key": google_key},
                    json=payload,
                )
            if resp.status_code == 200:
                audio_data = _b64.b64decode(resp.json()["audioContent"])
                logger.info("Google TTS: %d chars -> %d bytes voice=%s rate=%s",
                            len(text), len(audio_data), google_voice, rate)
                return audio_data
            logger.warning("Google TTS %d — falling back to edge-tts: %s",
                           resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Google TTS error (%s) — falling back to edge-tts", exc)

    import edge_tts
    communicate = edge_tts.Communicate(text, edge_voice, rate=rate)
    chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    audio_data = b"".join(chunks)
    logger.info("edge-tts: %d chars -> %d bytes voice=%s rate=%s",
                len(text), len(audio_data), edge_voice, rate)
    return audio_data

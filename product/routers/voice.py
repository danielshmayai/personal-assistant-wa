"""Tenant-scoped voice (STT/TTS) API for the product web app.

STT (Whisper transcription) needs no credentials and no per-scope data, so
it's available to any active tenant like chat (require_session only). TTS
is gated by the 'tts' module toggle since it represents the "voice replies"
feature a tenant opts into; app.voice.synthesize_speech resolves
GOOGLE_TTS_API_KEY per-tenant via runtime.get_secret (falls back to the
free edge-tts engine when absent — never the owner's key).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import Response

from product.deps import require_module, require_session
from product.tenancy.models import Tenant

logger = logging.getLogger("product.voice")
router = APIRouter()
_tts_gate = require_module("tts")


@router.post("/api/stt")
async def speech_to_text(file: UploadFile, _: Tenant = Depends(require_session)):
    from app import voice
    data = await file.read()
    try:
        text = await voice.transcribe_audio(data, file.content_type or "")
        return {"text": text}
    except ValueError as exc:
        code = {"empty_audio": 400, "audio_too_large": 413}.get(str(exc), 400)
        raise HTTPException(status_code=code, detail=str(exc))
    except Exception as exc:
        logger.exception("STT failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/tts")
async def text_to_speech(text: str = Query(...), _: Tenant = Depends(_tts_gate)):
    from app import voice
    try:
        audio_data = await voice.synthesize_speech(text)
        return Response(content=audio_data, media_type="audio/mpeg")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("TTS failed (Google + edge-tts both failed)")
        raise HTTPException(status_code=500, detail=str(exc))

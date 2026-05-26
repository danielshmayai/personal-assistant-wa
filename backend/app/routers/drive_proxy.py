"""Proxy endpoint: fetch a Drive file by ID and stream it to the browser."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.config import TEST_TOKEN
from app.google.auth import get_credentials
from app.google import drive as drive_api

router = APIRouter()


@router.get("/api/drive/proxy/{file_id}")
async def proxy_drive_file(
    file_id: str,
    chat_id: str = Query(...),
    token: str = Query(...),
):
    if not TEST_TOKEN or token != TEST_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    creds = get_credentials(chat_id)
    if not creds or not creds.valid:
        raise HTTPException(status_code=403, detail="Google not connected")

    try:
        data, mime_type = drive_api.download_file(creds, file_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Drive download failed: {exc}")

    return Response(content=data, media_type=mime_type)

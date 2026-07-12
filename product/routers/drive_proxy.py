"""Tenant-scoped Google Drive image/file proxy for the product web app.

Auth is the session cookie (sent automatically by the browser on same-origin
<img src="..."> requests — no query-string token needed, unlike the old
TEST_TOKEN-gated router). get_credentials("web") resolves the CALLING
tenant's own Google token via current_tenant_id (set by require_session);
Drive's API itself only returns files that token's account can access, so a
tenant can never fetch another tenant's or the owner's Drive file even if
they guess a file_id.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.google import drive as drive_api
from app.google.auth import get_credentials
from product.deps import require_session
from product.tenancy.models import Tenant

router = APIRouter()


@router.get("/api/drive/proxy/{file_id}")
async def proxy_drive_file(file_id: str, _: Tenant = Depends(require_session)):
    creds = get_credentials("web")
    if not creds or not creds.valid:
        raise HTTPException(status_code=403, detail="google_not_connected")

    try:
        # Sync/blocking Drive API call — off the event loop so one tenant's
        # slow download can't stall other tenants' concurrent requests.
        data, mime_type = await asyncio.to_thread(drive_api.download_file, creds, file_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"drive_download_failed: {exc}")

    return Response(content=data, media_type=mime_type)

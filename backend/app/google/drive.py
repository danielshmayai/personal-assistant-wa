"""
Core Google Drive API helpers.

Folder structure created under the user's Drive:
  PA/
    Photos/
      2026-04/   ← one sub-folder per calendar month
      2026-05/
    Documents/
      PDFs/
      Word/
      Spreadsheets/
      Receipts/
      Work/
      Personal/
      General/   ← default
"""

import io
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.config import USER_TIMEZONE

logger = logging.getLogger("pa.google.drive")

_PA_ROOT = "PA"

# MIME types treated as photos (go to PA/Photos)
PHOTO_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/heic",
    "image/heif",
}

# Auto-category map for documents
DOC_CATEGORY_MAP = {
    "application/pdf": "PDFs",
    "application/msword": "Word",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
    "application/vnd.ms-excel": "Spreadsheets",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Spreadsheets",
    "text/csv": "Spreadsheets",
    "application/vnd.ms-powerpoint": "Presentations",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "Presentations",
    "text/plain": "Text",
}


def _svc(creds: Credentials):
    return build("drive", "v3", credentials=creds)


def _find_folder(svc, name: str, parent_id: str | None) -> str | None:
    """Return the ID of an existing folder, or None if it doesn't exist."""
    q = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
        " and trashed=false"
    )
    if parent_id:
        q += f" and '{parent_id}' in parents"
    res = svc.files().list(q=q, fields="files(id)", pageSize=1).execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def _get_or_create_folder(svc, name: str, parent_id: str | None = None) -> str:
    fid = _find_folder(svc, name, parent_id)
    if fid:
        return fid
    meta: dict = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    folder = svc.files().create(body=meta, fields="id").execute()
    logger.info("Drive: created folder '%s'", name)
    return folder["id"]


def _resolve_path(svc, path_parts: list[str]) -> str:
    """Resolve (and create if missing) a nested folder path. Returns leaf folder ID."""
    parent_id = None
    for part in path_parts:
        parent_id = _get_or_create_folder(svc, part, parent_id)
    return parent_id  # type: ignore[return-value]


def _resolve_path_read_only(svc, path_parts: list[str]) -> str | None:
    """Resolve a nested folder path without creating anything. Returns None if missing."""
    parent_id = None
    for part in path_parts:
        fid = _find_folder(svc, part, parent_id)
        if not fid:
            return None
        parent_id = fid
    return parent_id


def _upload(svc, data: bytes, filename: str, mime_type: str, folder_id: str) -> str:
    """Upload bytes to Drive and return the webViewLink."""
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type)
    f = svc.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id,webViewLink",
    ).execute()
    return f.get("webViewLink", "")


def upload_photo(
    creds: Credentials,
    data: bytes,
    filename: str,
    mime_type: str,
    subfolder: str = "",
) -> str:
    """Upload a photo to PA/Photos/{subfolder}/ or PA/Photos/YYYY-MM/ when subfolder is empty."""
    svc = _svc(creds)
    bucket = subfolder.strip() if subfolder.strip() else datetime.now(tz=ZoneInfo(USER_TIMEZONE)).strftime("%Y-%m")
    folder_id = _resolve_path(svc, [_PA_ROOT, "Photos", bucket])
    link = _upload(svc, data, filename, mime_type, folder_id)
    logger.info("Drive: uploaded photo '%s' → PA/Photos/%s", filename, bucket)
    return link


def upload_document(
    creds: Credentials,
    data: bytes,
    filename: str,
    mime_type: str,
    category: str,
) -> str:
    """Upload a document to PA/Documents/{category}/. Returns webViewLink."""
    svc = _svc(creds)
    folder_id = _resolve_path(svc, [_PA_ROOT, "Documents", category])
    link = _upload(svc, data, filename, mime_type, folder_id)
    logger.info("Drive: uploaded document '%s' → PA/Documents/%s", filename, category)
    return link


def list_files(
    creds: Credentials, folder_path: str = "", max_results: int = 15
) -> list[dict]:
    """
    List files inside a PA sub-path. folder_path examples:
      ''               → root PA folder
      'Photos'         → all photo months
      'Photos/2026-04' → specific month
      'Documents/PDFs' → specific category
    """
    svc = _svc(creds)
    parts = [_PA_ROOT] + [p for p in folder_path.strip("/").split("/") if p]
    parent_id = _resolve_path_read_only(svc, parts)
    if not parent_id:
        return []
    q = f"trashed=false and '{parent_id}' in parents"
    res = svc.files().list(
        q=q,
        orderBy="createdTime desc",
        pageSize=max_results,
        fields="files(id,name,mimeType,createdTime,webViewLink)",
    ).execute()
    return res.get("files", [])

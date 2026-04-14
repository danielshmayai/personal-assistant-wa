"""
LangChain tools for Google Drive.
Media is downloaded from WAHA using the message_id provided by the webhook.
"""

import logging

import httpx
from langchain_core.tools import tool

from app.config import WAHA_API_KEY, WAHA_BASE_URL, WAHA_SESSION
from app.google import drive as drive_api
from app.google.auth import get_credentials
from app.google.drive import DOC_CATEGORY_MAP, PHOTO_MIME_TYPES

logger = logging.getLogger("pa.google.drive_tools")


async def _download_from_waha(message_id: str) -> tuple[bytes, str]:
    """
    Download media bytes from WAHA and return (data, mime_type).

    WAHA exposes: GET /api/{session}/messages/{messageId}/download
    """
    headers: dict[str, str] = {}
    if WAHA_API_KEY:
        headers["X-Api-Key"] = WAHA_API_KEY

    url = f"{WAHA_BASE_URL}/api/{WAHA_SESSION}/messages/{message_id}/download"
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.get(url, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(
                f"WAHA returned {r.status_code} when downloading message {message_id}"
            )
        mime_type = r.headers.get("content-type", "application/octet-stream").split(";")[0]
        return r.content, mime_type


def get_drive_tools(chat_id: str) -> list:

    @tool
    async def drive_save_photo(message_id: str, filename: str) -> str:
        """Save a photo received in WhatsApp to Google Drive (PA/Photos/YYYY-MM/).
        Use this when the user sends an image or photo and wants it saved to Drive.
        message_id comes from the [MEDIA ...] context tag. filename is the suggested filename."""
        creds = get_credentials(chat_id)
        if not creds or not creds.valid:
            return "Google is not connected. Call google_connect first, share the link, and try again after the user authenticates."
        try:
            data, mime_type = await _download_from_waha(message_id)
            link = drive_api.upload_photo(creds, data, filename, mime_type)
            return f"Photo saved to Drive (PA/Photos). View: {link}"
        except Exception as e:
            logger.exception("drive_save_photo failed for message_id=%s", message_id)
            return f"Failed to save photo: {e}"

    @tool
    async def drive_save_document(
        message_id: str, filename: str, category: str = "General"
    ) -> str:
        """Save a document/PDF/file received in WhatsApp to Google Drive (PA/Documents/{category}/).
        Common categories: PDFs, Word, Spreadsheets, Presentations, Receipts, Work, Personal, General.
        message_id comes from the [MEDIA ...] context tag. Auto-detect category from mime_type if unsure."""
        creds = get_credentials(chat_id)
        if not creds or not creds.valid:
            return "Google is not connected. Call google_connect first."
        try:
            data, mime_type = await _download_from_waha(message_id)
            # Auto-detect category from MIME if caller left it as "General"
            if category == "General" and mime_type in DOC_CATEGORY_MAP:
                category = DOC_CATEGORY_MAP[mime_type]
            link = drive_api.upload_document(creds, data, filename, mime_type, category)
            return f"Document saved to Drive (PA/Documents/{category}). View: {link}"
        except Exception as e:
            logger.exception("drive_save_document failed for message_id=%s", message_id)
            return f"Failed to save document: {e}"

    @tool
    def drive_list_files(folder: str = "") -> str:
        """List files saved to Google Drive.
        folder examples: 'Photos/2026-04', 'Documents/PDFs', 'Documents/Receipts', or empty for recent files."""
        creds = get_credentials(chat_id)
        if not creds or not creds.valid:
            return "Google is not connected. Call google_connect first."
        try:
            files = drive_api.list_files(creds, folder, max_results=15)
            if not files:
                return f"No files found in {folder or 'Drive'}."
            lines = [
                f"• {f['name']} ({f.get('mimeType', '').split('/')[-1]})"
                for f in files
            ]
            return "\n".join(lines)
        except Exception as e:
            logger.exception("drive_list_files failed")
            return f"Failed to list files: {e}"

    return [drive_save_photo, drive_save_document, drive_list_files]

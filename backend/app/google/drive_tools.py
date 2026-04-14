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

_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
_RECONNECT_MSG = (
    "Drive access is not granted yet. "
    "Please call google_connect, open the link, and reconnect your Google account — "
    "this will add the Drive permission to your existing Gmail/Calendar connection."
)


def _check_drive_scope(creds) -> str | None:
    """Return an error string if the Drive scope is missing, or None if all is good."""
    if not creds or not creds.valid:
        return "Google is not connected. Call google_connect first."
    if creds.scopes and not any("drive" in s for s in creds.scopes):
        return _RECONNECT_MSG
    return None


async def _http_get(url: str) -> tuple[bytes, str]:
    """Fetch bytes from a URL using WAHA auth headers."""
    headers: dict[str, str] = {}
    if WAHA_API_KEY:
        headers["X-Api-Key"] = WAHA_API_KEY
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        mime_type = r.headers.get("content-type", "application/octet-stream").split(";")[0]
        return r.content, mime_type


async def _download_from_waha(message_id: str) -> tuple[bytes, str]:
    """
    Return (bytes, mime_type) for a WAHA media message.

    Resolution order:
      1. In-memory cache populated by the webhook handler from _data.body (base64)
         — this is the normal path for WAHA WEBJS (free edition).
      2. mediaUrl stored in cache during webhook (WAHA serves the file directly).
      3. WAHA REST API fallback — tries multiple endpoint patterns for robustness.
    """
    from app.media_cache import retrieve

    cached = retrieve(message_id)
    if cached:
        if "data" in cached:
            logger.debug("drive: using cached bytes for msg %s", message_id[-12:])
            return cached["data"], cached["mime_type"]
        if "media_url" in cached:
            logger.debug("drive: downloading from cached mediaUrl for msg %s", message_id[-12:])
            return await _http_get(cached["media_url"])

    # Fallback: try known WAHA API endpoint patterns
    logger.info("drive: no cache hit for %s — trying WAHA API", message_id[-12:])
    candidates = [
        f"{WAHA_BASE_URL}/api/{WAHA_SESSION}/messages/{message_id}/download",
        f"{WAHA_BASE_URL}/api/messages/{WAHA_SESSION}/{message_id}/download",
        f"{WAHA_BASE_URL}/api/files/{WAHA_SESSION}/{message_id}",
    ]
    last_err: Exception = RuntimeError("no candidates")
    for url in candidates:
        try:
            data, mime = await _http_get(url)
            logger.info("drive: fallback download succeeded via %s", url)
            return data, mime
        except Exception as exc:
            last_err = exc
            logger.debug("drive: fallback %s → %s", url, exc)

    raise RuntimeError(
        f"Could not download media for message {message_id[-12:]}. "
        f"Last error: {last_err}. "
        "Make sure Google is connected and the message is recent."
    )


def get_drive_tools(chat_id: str) -> list:

    @tool
    async def drive_save_photo(message_id: str, filename: str, subfolder: str = "") -> str:
        """Save a photo or image received in WhatsApp to Google Drive.
        Saved under PA/Photos/{subfolder}/ when the user specifies a folder name,
        otherwise under PA/Photos/YYYY-MM/ (current month, auto-dated).
        IMPORTANT: if the user's caption mentions any folder or album name (e.g. 'screenshots',
        'work', 'vacation', 'family'), pass it as subfolder — do NOT ignore it.
        message_id comes from the [MEDIA ...] context tag."""
        creds = get_credentials(chat_id)
        err = _check_drive_scope(creds)
        if err:
            return err
        try:
            data, mime_type = await _download_from_waha(message_id)
            link = drive_api.upload_photo(creds, data, filename, mime_type, subfolder)
            dest = f"PA/Photos/{subfolder}" if subfolder else "PA/Photos (by date)"
            return f"Photo saved to Drive ({dest}). View: {link}"
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
        err = _check_drive_scope(creds)
        if err:
            return err
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
        err = _check_drive_scope(creds)
        if err:
            return err
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

"""General image tools for chat: vision analysis + Drive save with memory tagging.

The agent LLM is text-only — images arrive as [MEDIA id=… type=image …] tags and are
reachable only via message_id tools. These two tools cover the generic cases:

  analyze_image     — identify / describe / answer questions about any image
                      (bike model, plant species, data screenshots…).
  save_photo_tagged — upload to Drive AND write an Obsidian memory note with the link,
                      an AI description, and #tags, so the link is retrievable later by
                      simply asking (retrieve_context keyword search).
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from app.config import USER_TIMEZONE

logger = logging.getLogger("pa.image")

_ANALYZE_SYSTEM = """\
You are danidin's visual analyst. Reply in HEBREW, conversational WhatsApp style.

If the user asked a specific question — answer it precisely and concretely.
Otherwise: identify what the image shows as SPECIFICALLY as you can —
- objects: make/model/type (bike, car, appliance…), notable features, rough value class;
- animals/plants: species (common + scientific name when confident), care/identification notes;
- data screenshots/documents: what kind of screen it is and the actual key data readable in it;
- scenes/people: a short factual description.
Be honest about uncertainty ("נראה כמו…"). 2-6 sentences, no headers.

End with ONE short line offering the most relevant next action(s) only:
save to Drive + memory tag / log as workout / analyze as body scan / log as meal."""

_DESCRIBE_SYSTEM = """\
Describe this image for a searchable photo index. Reply in HEBREW with EXACTLY two lines:
line 1 — one concise sentence saying what the photo shows (specific: model/species/place if identifiable);
line 2 — 2-4 space-separated hashtags in Hebrew or English (e.g. #אופניים #screenshots #קבלה).
No other text."""


async def _vision(image_bytes: bytes, mime_type: str, system: str, user_text: str) -> str:
    """One multimodal Gemini call, returns raw text (idiom from fitness.parse_body_scan_image)."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from app.llm import get_gemini_llm, response_text

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_uri = f"data:{mime_type or 'image/jpeg'};base64,{b64}"
    llm = get_gemini_llm()
    resp = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=[
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]),
    ])
    return response_text(resp).strip()


def get_image_tools(chat_id: str) -> list:
    """Return image tools bound to *chat_id*."""

    @tool
    async def analyze_image(message_id: str, question: str = "") -> str:
        """Analyze an image the user sent with vision AI — identify objects (bike/car
        make & model, animal or plant species…), read data out of screenshots, describe
        scenes, or answer any question about the picture. Does NOT save anything.

        Use when a [MEDIA type=image …] tag arrives and:
        - the caption asks a question or asks to identify/analyze something
          ("מה זה?", "איזה דגם?", "איזה צמח זה?", "תנתח", "מה כתוב פה?"), OR
        - there is NO caption at all — analyze first and offer what to do next.

        message_id: from the [MEDIA ...] tag, unchanged.
        question: the user's caption/question, verbatim (empty if none).
        """
        from app.google.drive_tools import _download_from_waha

        try:
            data, mime = await _download_from_waha(message_id)
        except Exception as exc:
            return f"לא הצלחתי להוריד את התמונה: {exc}"
        try:
            prompt = question.strip() if question.strip() else "Analyze this image."
            return await _vision(data, mime, _ANALYZE_SYSTEM, prompt)
        except Exception as exc:
            logger.exception("analyze_image vision call failed")
            return f"ניתוח התמונה נכשל: {exc}"

    @tool
    async def save_photo_tagged(
        message_id: str,
        filename: str,
        subfolder: str = "",
        description: str = "",
        tags: str = "",
    ) -> str:
        """Save a photo to Google Drive AND tag it in memory (Obsidian) with the Drive
        link + description + hashtags, so the user can later just ask for the link
        ("שלוף לי את הקישור לתמונה של…") and it will be found.

        Use when the user asks to save/keep an image ("שמור", "תשמור בתיקיית…").
        Pass the caption's folder name as subfolder, and derive description/tags from the
        caption when it describes the photo; leave them empty to auto-describe with AI.

        message_id: from the [MEDIA ...] tag, unchanged.
        filename: descriptive filename with extension (e.g. bike_trek_2026.jpg).
        subfolder: album/folder under PA/Photos/ (empty = auto by month).
        description: short searchable description; empty → AI generates one.
        tags: space-separated #hashtags; empty → AI suggests.
        """
        from app.google import drive as drive_api
        from app.google.auth import get_credentials
        from app.google.drive_tools import _check_drive_scope, _download_from_waha
        from app.memory import obsidian

        creds = get_credentials(chat_id)
        err = _check_drive_scope(creds)
        if err:
            return err
        try:
            data, mime = await _download_from_waha(message_id)
        except Exception as exc:
            return f"לא הצלחתי להוריד את התמונה: {exc}"

        try:
            link = drive_api.upload_photo(creds, data, filename, mime, subfolder)
        except Exception as exc:
            logger.exception("save_photo_tagged upload failed")
            return f"השמירה לדרייב נכשלה: {exc}"

        # Always tag in memory — auto-describe when the caption gave nothing to index.
        if not description.strip():
            try:
                auto = await _vision(data, mime, _DESCRIBE_SYSTEM, "Describe for the photo index.")
                lines = [l.strip() for l in auto.splitlines() if l.strip()]
                description = lines[0] if lines else filename
                if not tags.strip() and len(lines) > 1 and "#" in lines[1]:
                    tags = lines[1]
            except Exception:
                logger.warning("photo auto-describe failed — indexing by filename only", exc_info=True)
                description = filename

        dest = f"PA/Photos/{subfolder.strip()}" if subfolder.strip() else "PA/Photos (לפי חודש)"
        month = datetime.now(tz=ZoneInfo(USER_TIMEZONE)).strftime("%Y-%m")
        note = f"📷 {filename} — {description}\nDrive: {link}"
        if tags.strip():
            note += f"\n{tags.strip()}"
        try:
            obsidian.save_fact("Photos", month, note)
            memory_line = "🧠 תויג בזיכרון — אפשר לבקש ממני את הקישור בכל שלב."
        except Exception:
            logger.exception("photo memory tagging failed")
            memory_line = "⚠️ נשמר בדרייב אבל התיוג בזיכרון נכשל."

        return f"✅ התמונה נשמרה ({dest})\n{description}\nקישור: {link}\n{memory_line}"

    return [analyze_image, save_photo_tagged]

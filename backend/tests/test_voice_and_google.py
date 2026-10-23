"""Voice-note transcription (Gemini STT) and the deeper Gmail/Calendar tools."""
import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


# ── Speech to text ───────────────────────────────────────────────────────────

def _gemini_reply(text):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    return resp


def test_gemini_stt_sends_inline_audio_with_the_scope_key():
    from app import voice
    client = MagicMock()
    client.post = AsyncMock(return_value=_gemini_reply(" שלום עולם "))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("app.runtime.get_secret", side_effect=lambda k: "k123" if k == "GEMINI_API_KEY" else ""), \
         patch("httpx.AsyncClient", return_value=client):
        text = _run(voice.transcribe_audio(b"OGGDATA", "audio/ogg; codecs=opus"))
    assert text == "שלום עולם"
    url = client.post.call_args.args[0]
    kwargs = client.post.call_args.kwargs
    assert ":generateContent" in url
    assert kwargs["headers"] == {"x-goog-api-key": "k123"}
    inline = kwargs["json"]["contents"][0]["parts"][1]["inline_data"]
    assert inline["mime_type"] == "audio/ogg"
    assert base64.b64decode(inline["data"]) == b"OGGDATA"


def test_stt_falls_back_to_whisper_without_a_key():
    from app import voice
    seg = MagicMock(text="hello")
    model = MagicMock()
    model.transcribe.return_value = ([seg], None)
    with patch("app.runtime.get_secret", return_value=""), \
         patch.object(voice, "_get_whisper", return_value=model):
        assert _run(voice.transcribe_audio(b"x", "audio/webm")) == "hello"


def test_whatsapp_voice_note_is_replaced_by_its_transcript():
    from app import whatsapp
    tag = "[MEDIA id=false_972@c.us_ABC type=ptt filename=ptt_1.ogg mime=audio/ogg; codecs=opus]"
    msg = f"{tag}\n(no caption — follow the media routing rules: image → analyze first, document → save)"
    with patch("app.google.drive_tools._download_from_waha", AsyncMock(return_value=(b"a", "audio/ogg"))), \
         patch("app.voice.transcribe_audio", AsyncMock(return_value="תזכיר לי מחר להתקשר לדנה")) as stt:
        out = _run(whatsapp._transcribe_voice_note(msg))
    assert out == "🎤 תזכיר לי מחר להתקשר לדנה"
    assert stt.call_args.args[1].startswith("audio/ogg")


def test_failed_transcription_tells_the_agent():
    from app import whatsapp
    msg = "[MEDIA id=X type=ptt filename=a.ogg mime=audio/ogg]\n"
    with patch("app.google.drive_tools._download_from_waha", AsyncMock(side_effect=RuntimeError("gone"))):
        out = _run(whatsapp._transcribe_voice_note(msg))
    assert "could not be transcribed" in out


def test_non_audio_media_is_left_alone():
    from app import whatsapp
    msg = "[MEDIA id=X type=image filename=a.jpg mime=image/jpeg]\nמה זה?"
    assert _run(whatsapp._transcribe_voice_note(msg)) == msg


# ── Gmail / Calendar ─────────────────────────────────────────────────────────

def test_gmail_search_uses_query_across_all_mail():
    from app.google import gmail
    svc = MagicMock()
    msgs = svc.users.return_value.messages.return_value
    msgs.list.return_value.execute.return_value = {"messages": [{"id": "m1"}]}
    msgs.get.return_value.execute.return_value = {
        "snippet": "Invoice attached",
        "payload": {"headers": [{"name": "From", "value": "dana@x.com"},
                                {"name": "Subject", "value": "Invoice"}]},
    }
    with patch.object(gmail, "get_credentials", return_value=object()), \
         patch.object(gmail, "build", return_value=svc):
        out = gmail.read_emails("c", 5, "from:dana invoice")
    kwargs = msgs.list.call_args.kwargs
    assert kwargs["q"] == "from:dana invoice" and "labelIds" not in kwargs
    assert "Id: m1" in out and "Invoice" in out


def test_gmail_read_message_returns_plain_text_body():
    from app.google import gmail
    body = base64.urlsafe_b64encode("שלום, מצורפת חשבונית".encode()).decode()
    svc = MagicMock()
    svc.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "payload": {"mimeType": "multipart/alternative",
                    "headers": [{"name": "Subject", "value": "Invoice"}],
                    "parts": [{"mimeType": "text/plain", "body": {"data": body}}]},
    }
    with patch.object(gmail, "get_credentials", return_value=object()), \
         patch.object(gmail, "build", return_value=svc):
        out = gmail.read_message("c", "m1")
    assert "Subject: Invoice" in out and "מצורפת חשבונית" in out


def test_calendar_update_moves_start_and_keeps_duration():
    from app.google import calendar
    svc = MagicMock()
    events = svc.events.return_value
    events.get.return_value.execute.return_value = {
        "start": {"dateTime": "2026-10-25T10:00:00+03:00"},
        "end": {"dateTime": "2026-10-25T11:30:00+03:00"},
    }
    events.patch.return_value.execute.return_value = {
        "summary": "Dentist", "start": {"dateTime": "2026-10-26T09:00:00+03:00"}}
    with patch.object(calendar, "get_credentials", return_value=object()), \
         patch.object(calendar, "build", return_value=svc):
        out = calendar.update_event("c", "ev1", start_datetime="2026-10-26T09:00:00+03:00")
    body = events.patch.call_args.kwargs["body"]
    assert events.patch.call_args.kwargs["eventId"] == "ev1"
    assert body["end"]["dateTime"] == "2026-10-26T10:30:00+03:00"
    assert "summary" not in body
    assert "updated" in out


def test_calendar_delete_and_list_ids():
    from app.google import calendar
    svc = MagicMock()
    events = svc.events.return_value
    events.list.return_value.execute.return_value = {"items": [
        {"id": "ev9", "summary": "Standup", "start": {"dateTime": "2026-10-24T09:00:00Z"}}]}
    with patch.object(calendar, "get_credentials", return_value=object()), \
         patch.object(calendar, "build", return_value=svc):
        listed = calendar.list_events("c", 10, days_ahead=7, query="standup")
        deleted = calendar.delete_event("c", "ev9")
    kwargs = events.list.call_args.kwargs
    assert "timeMax" in kwargs and kwargs["q"] == "standup"
    assert "Id: ev9" in listed
    events.delete.assert_called_once_with(calendarId="primary", eventId="ev9")
    assert deleted == "Event deleted."

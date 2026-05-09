"""
Tests for the async message worker queue.

Covers:
- Webhook returns 202 immediately (no graph call in request path)
- Webhook rejects requests with wrong/missing WEBHOOK_SECRET (per-request enforcement)
- Worker processes message and invokes graph + send
- Duplicate message_id is ignored (dedup)
- Worker retries on transient error, gives up after max attempts
- Worker shutdown is clean (CancelledError handled, in-flight message logged)
"""

import asyncio
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_app_modules():
    """Remove app.* from sys.modules before and after each test."""
    _clean()
    yield
    _clean()


def _clean():
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


# Fake test token values — deliberately not real credentials.
_FAKE_TOKEN = "tok-abc123"
_FAKE_WRONG = "tok-wrong99"


# ---------------------------------------------------------------------------
# 0. WEBHOOK_SECRET per-request enforcement
# ---------------------------------------------------------------------------

def test_webhook_rejects_wrong_token():
    """Webhook must return 403 when WEBHOOK_SECRET is set and the query param doesn't match."""
    body = {
        "event": "message.any",
        "payload": {"id": "msg-sec-001", "fromMe": True, "from": "x@c.us", "to": "x@c.us", "body": "hi", "hasMedia": False},
    }

    async def run():
        with patch("app.whatsapp.WEBHOOK_SECRET", _FAKE_TOKEN):
            from app.whatsapp import waha_webhook
            mock_request = AsyncMock()
            mock_request.json = AsyncMock(return_value=body)
            mock_request.client = MagicMock()
            response = await waha_webhook(mock_request, secret=_FAKE_WRONG)
        assert response.status_code == 403

    asyncio.get_event_loop().run_until_complete(run())


def test_webhook_rejects_empty_token():
    """Webhook must return 403 when WEBHOOK_SECRET is set and query param is absent."""
    body = {
        "event": "message.any",
        "payload": {"id": "msg-sec-002", "fromMe": True, "from": "x@c.us", "to": "x@c.us", "body": "hi", "hasMedia": False},
    }

    async def run():
        with patch("app.whatsapp.WEBHOOK_SECRET", _FAKE_TOKEN):
            from app.whatsapp import waha_webhook
            mock_request = AsyncMock()
            mock_request.json = AsyncMock(return_value=body)
            mock_request.client = MagicMock()
            response = await waha_webhook(mock_request, secret="")
        assert response.status_code == 403

    asyncio.get_event_loop().run_until_complete(run())


def test_webhook_accepts_valid_token():
    """Webhook must process the request when the correct WEBHOOK_SECRET is supplied."""
    body = {
        "event": "message.any",
        "payload": {"id": "msg-sec-003", "fromMe": True, "from": "x@c.us", "to": "x@c.us", "body": "hi", "hasMedia": False},
    }

    async def run():
        with (
            patch("app.whatsapp.WEBHOOK_SECRET", _FAKE_TOKEN),
            patch("app.whatsapp.MY_WHATSAPP_ID", "x@c.us"),
            patch("app.whatsapp.enqueue", return_value=True),
        ):
            from app.whatsapp import waha_webhook
            mock_request = AsyncMock()
            mock_request.json = AsyncMock(return_value=body)
            mock_request.client = MagicMock()
            response = await waha_webhook(mock_request, secret=_FAKE_TOKEN)
        assert response.status_code in (200, 202)

    asyncio.get_event_loop().run_until_complete(run())


# ---------------------------------------------------------------------------
# 1. Webhook returns 202 immediately for self-chat
# ---------------------------------------------------------------------------

def test_webhook_self_chat_returns_202_without_calling_graph():
    """Self-chat webhook must enqueue and return 202; graph must NOT be called."""
    body = {
        "event": "message.any",
        "payload": {
            "id": "msg-sc-001",
            "fromMe": True,
            "from": "972501234567@c.us",
            "to": "972501234567@c.us",
            "body": "remind me to call Dan",
            "hasMedia": False,
        },
    }

    async def run():
        with (
            patch("app.whatsapp.WEBHOOK_SECRET", ""),
            patch("app.whatsapp.MY_WHATSAPP_ID", "972501234567@c.us"),
            patch("app.whatsapp.enqueue", return_value=True) as mock_enqueue,
        ):
            from app.whatsapp import waha_webhook

            mock_request = AsyncMock()
            mock_request.json = AsyncMock(return_value=body)
            mock_request.client = MagicMock()

            response = await waha_webhook(mock_request, secret="")

        assert response.status_code == 202, f"Expected 202, got {response.status_code}"
        mock_enqueue.assert_called_once()
        args = mock_enqueue.call_args[0]
        assert args[0] == "msg-sc-001"           # message_id
        assert args[1] == "972501234567@c.us"    # chat_id

    asyncio.get_event_loop().run_until_complete(run())


def test_webhook_trigger_returns_202_without_calling_graph():
    """Group trigger webhook must enqueue and return 202; graph must NOT be called."""
    body = {
        "event": "message",
        "payload": {
            "id": "msg-trig-001",
            "fromMe": False,
            "from": "99999@c.us",
            "to": "group-123@g.us",
            "body": "@danidin what's the weather?",
            "hasMedia": False,
        },
    }

    async def run():
        with (
            patch("app.whatsapp.WEBHOOK_SECRET", ""),
            patch("app.whatsapp.MY_WHATSAPP_ID", ""),
            patch("app.whatsapp.enqueue", return_value=True) as mock_enqueue,
            patch("app.leads.is_watched", return_value=False),
        ):
            from app.whatsapp import waha_webhook

            mock_request = AsyncMock()
            mock_request.json = AsyncMock(return_value=body)
            mock_request.client = MagicMock()

            response = await waha_webhook(mock_request, secret="")

        assert response.status_code == 202
        mock_enqueue.assert_called_once()
        # Stripped text (trigger prefix removed) should be passed to the worker
        enqueued_text = mock_enqueue.call_args[0][2]
        assert "what's the weather?" in enqueued_text
        assert "@danidin" not in enqueued_text

    asyncio.get_event_loop().run_until_complete(run())


def test_webhook_non_trigger_returns_200_and_does_not_enqueue():
    """Messages without trigger prefix must be ignored (200, no enqueue)."""
    body = {
        "event": "message",
        "payload": {
            "id": "msg-ignore-001",
            "fromMe": False,
            "from": "99999@c.us",
            "to": "group-123@g.us",
            "body": "hey what's up?",
            "hasMedia": False,
        },
    }

    async def run():
        with (
            patch("app.whatsapp.WEBHOOK_SECRET", ""),
            patch("app.whatsapp.MY_WHATSAPP_ID", ""),
            patch("app.whatsapp.enqueue", return_value=True) as mock_enqueue,
            patch("app.leads.is_watched", return_value=False),
        ):
            from app.whatsapp import waha_webhook

            mock_request = AsyncMock()
            mock_request.json = AsyncMock(return_value=body)
            mock_request.client = MagicMock()

            response = await waha_webhook(mock_request, secret="")

        assert response.status_code == 200
        mock_enqueue.assert_not_called()

    asyncio.get_event_loop().run_until_complete(run())


# ---------------------------------------------------------------------------
# 2. Dedup — duplicate message_id ignored
# ---------------------------------------------------------------------------

def test_enqueue_returns_true_for_new_message_id():
    from app.worker import enqueue, _processed_ids
    _processed_ids.clear()
    result = enqueue("msg-new-001", "chat-001", "hello")
    assert result is True
    assert "msg-new-001" in _processed_ids


def test_enqueue_returns_false_for_duplicate_message_id():
    from app.worker import enqueue, _processed_ids
    _processed_ids.clear()
    enqueue("msg-dup", "chat-001", "first message")
    result = enqueue("msg-dup", "chat-001", "second message")
    assert result is False


def test_enqueue_empty_message_id_always_passes():
    """Empty message_id must never dedup — both calls should succeed."""
    from app.worker import enqueue, _processed_ids
    _processed_ids.clear()
    assert enqueue("", "chat-001", "first") is True
    assert enqueue("", "chat-001", "second") is True


def test_enqueue_prunes_expired_dedup_entries():
    """Entries older than 1 hour must be pruned on the next enqueue call."""
    from app.worker import enqueue, _processed_ids
    _processed_ids.clear()
    _processed_ids["old-msg"] = time.time() - 7200   # 2 hours ago
    enqueue("trigger-prune", "chat-001", "text")
    assert "old-msg" not in _processed_ids


def test_duplicate_message_does_not_reach_queue():
    """A duplicate message_id must not add an item to the asyncio queue."""
    from app.worker import enqueue, _processed_ids, _queue
    _processed_ids.clear()
    while not _queue.empty():
        _queue.get_nowait()

    enqueue("msg-q-dup", "chat-001", "first")
    depth_after_first = _queue.qsize()
    enqueue("msg-q-dup", "chat-001", "second")
    assert _queue.qsize() == depth_after_first  # no second item added


# ---------------------------------------------------------------------------
# 3. Worker processes a message end-to-end
# ---------------------------------------------------------------------------

def test_worker_processes_message_and_sends_reply():
    """_process_one must call _process_message then send_whatsapp_message."""
    from app.worker import _Msg, _process_one

    process_calls = []
    send_calls = []

    async def fake_process(text, chat_id):
        process_calls.append((text, chat_id))
        return "[ *danidin* ] pong"

    async def fake_send(chat_id, text):
        send_calls.append((chat_id, text))
        return True

    msg = _Msg(message_id="msg-ok-001", chat_id="chat-ok", full_text="ping")

    with (
        patch("app.whatsapp._process_message", side_effect=fake_process),
        patch("app.whatsapp.send_whatsapp_message", side_effect=fake_send),
    ):
        asyncio.get_event_loop().run_until_complete(_process_one(msg))

    assert process_calls == [("ping", "chat-ok")]
    assert send_calls == [("chat-ok", "[ *danidin* ] pong")]


# ---------------------------------------------------------------------------
# 4. Retry on transient error, give up after max attempts
# ---------------------------------------------------------------------------

def test_worker_retries_send_on_transient_error_then_succeeds():
    """Worker must retry after send failures and succeed on the third attempt."""
    from app.worker import _Msg, _process_one

    send_attempt = 0

    async def fake_process(text, chat_id):
        return "reply"

    async def flaky_send(chat_id, text):
        nonlocal send_attempt
        send_attempt += 1
        if send_attempt < 3:
            raise OSError("WAHA timeout")
        return True

    msg = _Msg(message_id="msg-retry-001", chat_id="chat-retry", full_text="hello")

    with (
        patch("app.worker.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        patch("app.whatsapp._process_message", side_effect=fake_process),
        patch("app.whatsapp.send_whatsapp_message", side_effect=flaky_send),
    ):
        asyncio.get_event_loop().run_until_complete(_process_one(msg))

    assert send_attempt == 3
    # Slept before attempt 2 (1s) and attempt 3 (5s)
    assert mock_sleep.call_count == 2
    assert mock_sleep.call_args_list[0][0][0] == 1
    assert mock_sleep.call_args_list[1][0][0] == 5


def test_worker_gives_up_after_max_attempts_and_logs_error(caplog):
    """Worker must stop after _MAX_ATTEMPTS without raising, and log an error."""
    import logging
    from app.worker import _Msg, _MAX_ATTEMPTS, _process_one

    send_attempt = 0

    async def fake_process(text, chat_id):
        return "reply"

    async def always_fail(chat_id, text):
        nonlocal send_attempt
        send_attempt += 1
        raise RuntimeError("permanent WAHA failure")

    msg = _Msg(message_id="msg-fail-001", chat_id="chat-fail", full_text="hello")

    with (
        patch("app.worker.asyncio.sleep", new_callable=AsyncMock),
        patch("app.whatsapp._process_message", side_effect=fake_process),
        patch("app.whatsapp.send_whatsapp_message", side_effect=always_fail),
        caplog.at_level(logging.ERROR, logger="pa.worker"),
    ):
        asyncio.get_event_loop().run_until_complete(_process_one(msg))

    assert send_attempt == _MAX_ATTEMPTS
    assert any("giving up" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 5. Worker shutdown — CancelledError handled cleanly
# ---------------------------------------------------------------------------

def test_worker_shutdown_cancels_cleanly():
    """stop_worker must cancel the task and await it without raising."""
    from app.worker import start_worker, stop_worker, _worker_task

    async def run():
        task = start_worker()
        assert not task.done()
        await stop_worker()
        assert task.done()

    asyncio.get_event_loop().run_until_complete(run())


def test_worker_status_reports_running_and_queue_depth():
    """worker_status must reflect actual task state and queue depth."""
    from app.worker import worker_status, start_worker, stop_worker, _queue

    async def run():
        # Before start
        before = worker_status()
        assert before["running"] is False

        start_worker()
        running = worker_status()
        assert running["running"] is True
        assert isinstance(running["queue_depth"], int)

        await stop_worker()
        after = worker_status()
        assert after["running"] is False

    asyncio.get_event_loop().run_until_complete(run())

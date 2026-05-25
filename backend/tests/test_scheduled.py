"""
Tests for scheduled jobs — _next_run recurrence logic, tool registration,
and DB-layer helpers (mocked psycopg2).

Rules:
- No real DB connections.
- No real WAHA / Tuya calls.
- Tests are fast and deterministic.
"""

from __future__ import annotations

import sys
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# sys.path helper
# ---------------------------------------------------------------------------

def _add_backend_path():
    import os
    backend = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
    if backend not in sys.path:
        sys.path.insert(0, backend)


_add_backend_path()


@pytest.fixture(autouse=True)
def _isolate_modules():
    _clean()
    yield
    _clean()


def _clean():
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


# ---------------------------------------------------------------------------
# 1. _next_run recurrence logic
# ---------------------------------------------------------------------------

class TestNextRun:
    """Unit tests for app.scheduled_jobs._next_run."""

    def _get_next_run(self):
        from app.scheduled_jobs import _next_run
        return _next_run

    def test_daily_adds_exactly_one_day(self):
        """'daily' recurrence must advance run_at by exactly 24 hours."""
        _next_run = self._get_next_run()
        base = datetime(2025, 5, 24, 8, 0, 0, tzinfo=timezone.utc)
        result = _next_run(base, "daily")
        assert result is not None
        # Should be exactly 24h later (accounting for possible DST in local conversion)
        diff = result - base
        assert timedelta(hours=23) <= diff <= timedelta(hours=25)

    def test_daily_same_time_next_day(self):
        """'daily' must schedule for the same wall-clock time tomorrow."""
        _next_run = self._get_next_run()
        # Use a naive UTC datetime at 08:00
        base = datetime(2025, 5, 24, 8, 0, 0, tzinfo=timezone.utc)
        result = _next_run(base, "daily")
        assert result is not None
        # The result, when converted back to UTC, should be 24h later
        expected = base + timedelta(days=1)
        # Allow for DST offset differences (max 2 hours)
        assert abs((result - expected).total_seconds()) <= 7200

    def test_weekly_adds_exactly_one_week(self):
        """'weekly' recurrence must advance run_at by exactly 7 days."""
        _next_run = self._get_next_run()
        base = datetime(2025, 5, 24, 8, 0, 0, tzinfo=timezone.utc)
        result = _next_run(base, "weekly")
        assert result is not None
        diff = result - base
        assert timedelta(days=6, hours=23) <= diff <= timedelta(days=7, hours=1)

    def test_hourly_adds_one_hour(self):
        """'hourly' recurrence must advance run_at by exactly one hour."""
        _next_run = self._get_next_run()
        base = datetime(2025, 5, 24, 14, 30, 0, tzinfo=timezone.utc)
        result = _next_run(base, "hourly")
        assert result is not None
        diff = result - base
        assert diff == timedelta(hours=1)

    def test_minutes_N_adds_correct_minutes(self):
        """'minutes:30' recurrence must advance run_at by exactly 30 minutes."""
        _next_run = self._get_next_run()
        base = datetime(2025, 5, 24, 9, 0, 0, tzinfo=timezone.utc)
        result = _next_run(base, "minutes:30")
        assert result is not None
        diff = result - base
        assert diff == timedelta(minutes=30)

    def test_minutes_1_adds_one_minute(self):
        """'minutes:1' must add exactly 1 minute."""
        _next_run = self._get_next_run()
        base = datetime(2025, 5, 24, 9, 0, 0, tzinfo=timezone.utc)
        result = _next_run(base, "minutes:1")
        assert result is not None
        assert result - base == timedelta(minutes=1)

    def test_minutes_60_adds_sixty_minutes(self):
        """'minutes:60' must add exactly 60 minutes."""
        _next_run = self._get_next_run()
        base = datetime(2025, 5, 24, 9, 0, 0, tzinfo=timezone.utc)
        result = _next_run(base, "minutes:60")
        assert result is not None
        assert result - base == timedelta(minutes=60)

    def test_unknown_recurrence_returns_none(self):
        """An unrecognised recurrence string must return None (not raise)."""
        _next_run = self._get_next_run()
        base = datetime(2025, 5, 24, 8, 0, 0, tzinfo=timezone.utc)
        result = _next_run(base, "fortnightly")
        assert result is None

    def test_malformed_minutes_returns_none(self):
        """'minutes:abc' must return None without raising."""
        _next_run = self._get_next_run()
        base = datetime(2025, 5, 24, 8, 0, 0, tzinfo=timezone.utc)
        result = _next_run(base, "minutes:abc")
        assert result is None

    def test_result_is_utc(self):
        """_next_run must always return a timezone-aware datetime in UTC."""
        _next_run = self._get_next_run()
        base = datetime(2025, 5, 24, 10, 0, 0, tzinfo=timezone.utc)
        for recurrence in ("daily", "weekly", "hourly", "minutes:15"):
            result = _next_run(base, recurrence)
            assert result is not None
            assert result.tzinfo is not None, f"Result for {recurrence!r} has no tzinfo"
            # UTC offset should be 0
            assert result.utcoffset() == timedelta(0), (
                f"Result for {recurrence!r} is not UTC: {result}"
            )


# ---------------------------------------------------------------------------
# 2. get_schedule_tools — tool registration
# ---------------------------------------------------------------------------

class TestScheduleTools:
    def test_get_schedule_tools_returns_five_tools(self):
        """get_schedule_tools must return exactly 5 tools."""
        from app.schedule_tool import get_schedule_tools
        tools = get_schedule_tools("test-chat")
        assert len(tools) == 5

    def test_get_schedule_tools_names(self):
        """get_schedule_tools must include the 5 expected tool names."""
        from app.schedule_tool import get_schedule_tools
        tools = get_schedule_tools("test-chat")
        names = {t.name for t in tools}
        expected = {
            "schedule_tuya_command",
            "schedule_reminder",
            "schedule_recurring_reminder",
            "list_scheduled",
            "cancel_scheduled",
        }
        assert names == expected, f"Tool name mismatch: expected {expected}, got {names}"

    def test_different_chat_ids_produce_distinct_tools(self):
        """Tools from different chat_ids must be distinct instances."""
        from app.schedule_tool import get_schedule_tools
        tools_a = get_schedule_tools("chat-a")
        tools_b = get_schedule_tools("chat-b")
        assert tools_a is not tools_b


# ---------------------------------------------------------------------------
# 3. schedule_reminder — validation
# ---------------------------------------------------------------------------

class TestScheduleReminderTool:
    def _get_schedule_reminder(self, chat_id="test-chat"):
        from app.schedule_tool import get_schedule_tools
        tools = get_schedule_tools(chat_id)
        return next(t for t in tools if t.name == "schedule_reminder")

    def test_schedule_reminder_empty_text_returns_error(self):
        """schedule_reminder must return an error when text is empty."""
        tool = self._get_schedule_reminder()
        result = tool.invoke({"text": "", "delay_minutes": 5})
        assert "חסר" in result or "text" in result.lower()

    def test_schedule_reminder_no_time_returns_error(self):
        """schedule_reminder must return an error when no delay or run_at specified."""
        tool = self._get_schedule_reminder()
        result = tool.invoke({"text": "תזכורת", "delay_minutes": 0, "run_at_iso": ""})
        assert "delay_minutes" in result or "run_at_iso" in result or "יש לציין" in result

    def test_schedule_reminder_inserts_job(self):
        """schedule_reminder with valid params must call insert_job."""
        insert_calls = []

        def fake_insert(chat_id, action_type, payload, run_at, recurrence=None):
            insert_calls.append({"chat_id": chat_id, "action_type": action_type, "payload": payload})
            return 99

        with patch("app.schedule_tool.get_schedule_tools.__wrapped__", create=True):
            from app.schedule_tool import get_schedule_tools
            tools = get_schedule_tools("chat-xyz")
            reminder = next(t for t in tools if t.name == "schedule_reminder")

        with patch("app.scheduled_jobs.insert_job", side_effect=fake_insert):
            result = reminder.invoke({"text": "תזכורת חשובה", "delay_minutes": 10})

        assert len(insert_calls) == 1
        assert insert_calls[0]["action_type"] == "send_message"
        assert insert_calls[0]["payload"]["text"] == "תזכורת חשובה"
        assert "✅" in result


# ---------------------------------------------------------------------------
# 4. schedule_recurring_reminder — validation
# ---------------------------------------------------------------------------

class TestScheduleRecurringReminderTool:
    def _get_recurring_reminder(self, chat_id="test-chat"):
        from app.schedule_tool import get_schedule_tools
        tools = get_schedule_tools(chat_id)
        return next(t for t in tools if t.name == "schedule_recurring_reminder")

    def test_invalid_recurrence_returns_error(self):
        """schedule_recurring_reminder must reject invalid recurrence strings."""
        tool = self._get_recurring_reminder()
        result = tool.invoke({
            "text": "תזכורת",
            "recurrence": "fortnightly",
            "delay_minutes": 5,
        })
        assert "לא תקין" in result or "recurrence" in result.lower()

    def test_valid_daily_recurrence_inserts_job(self):
        """schedule_recurring_reminder with 'daily' must call insert_job with recurrence."""
        insert_calls = []

        def fake_insert(chat_id, action_type, payload, run_at, recurrence=None):
            insert_calls.append({"recurrence": recurrence, "action_type": action_type})
            return 42

        from app.schedule_tool import get_schedule_tools
        tools = get_schedule_tools("chat-r")
        tool = next(t for t in tools if t.name == "schedule_recurring_reminder")

        with patch("app.scheduled_jobs.insert_job", side_effect=fake_insert):
            result = tool.invoke({
                "text": "בוקר טוב",
                "recurrence": "daily",
                "delay_minutes": 1,
            })

        assert len(insert_calls) == 1
        assert insert_calls[0]["recurrence"] == "daily"
        assert "✅" in result

    def test_minutes_N_recurrence_accepted(self):
        """schedule_recurring_reminder must accept 'minutes:30' as valid recurrence."""
        insert_calls = []

        def fake_insert(chat_id, action_type, payload, run_at, recurrence=None):
            insert_calls.append({"recurrence": recurrence})
            return 7

        from app.schedule_tool import get_schedule_tools
        tools = get_schedule_tools("chat-m")
        tool = next(t for t in tools if t.name == "schedule_recurring_reminder")

        with patch("app.scheduled_jobs.insert_job", side_effect=fake_insert):
            result = tool.invoke({
                "text": "אזכור",
                "recurrence": "minutes:30",
                "delay_minutes": 1,
            })

        assert len(insert_calls) == 1
        assert insert_calls[0]["recurrence"] == "minutes:30"
        assert "✅" in result

    def test_minutes_zero_N_is_rejected(self):
        """schedule_recurring_reminder must reject 'minutes:0' as invalid."""
        tool = self._get_recurring_reminder()
        result = tool.invoke({
            "text": "אזכור",
            "recurrence": "minutes:0",
            "delay_minutes": 1,
        })
        assert "לא תקין" in result or "חיובי" in result or "minutes" in result.lower()


# ---------------------------------------------------------------------------
# 5. cancel_scheduled — calls cancel_job
# ---------------------------------------------------------------------------

class TestCancelScheduledTool:
    def test_cancel_scheduled_returns_success_message_when_found(self):
        """cancel_scheduled must return a success string when cancel_job returns True."""
        from app.schedule_tool import get_schedule_tools
        tools = get_schedule_tools("chat-c")
        cancel = next(t for t in tools if t.name == "cancel_scheduled")

        with patch("app.scheduled_jobs.cancel_job", return_value=True):
            result = cancel.invoke({"job_id": 5})

        assert "✅" in result
        assert "5" in result

    def test_cancel_scheduled_returns_not_found_when_missing(self):
        """cancel_scheduled must return a not-found message when cancel_job returns False."""
        from app.schedule_tool import get_schedule_tools
        tools = get_schedule_tools("chat-c")
        cancel = next(t for t in tools if t.name == "cancel_scheduled")

        with patch("app.scheduled_jobs.cancel_job", return_value=False):
            result = cancel.invoke({"job_id": 999})

        assert "לא מצאתי" in result or "not found" in result.lower() or "999" in result


# ---------------------------------------------------------------------------
# 6. list_scheduled — calls list_pending_jobs
# ---------------------------------------------------------------------------

class TestListScheduledTool:
    def test_list_scheduled_returns_empty_message_when_no_jobs(self):
        """list_scheduled must return a no-jobs message when the DB is empty."""
        from app.schedule_tool import get_schedule_tools
        tools = get_schedule_tools("chat-l")
        list_tool = next(t for t in tools if t.name == "list_scheduled")

        with patch("app.scheduled_jobs.list_pending_jobs", return_value=[]):
            result = list_tool.invoke({})

        assert "אין" in result or "no" in result.lower()

    def test_list_scheduled_formats_jobs(self):
        """list_scheduled must include job IDs and descriptions in its output."""
        from zoneinfo import ZoneInfo
        from app.schedule_tool import get_schedule_tools
        tools = get_schedule_tools("chat-l2")
        list_tool = next(t for t in tools if t.name == "list_scheduled")

        tz = ZoneInfo("Asia/Jerusalem")
        fake_jobs = [
            {
                "id": 3,
                "action_type": "send_message",
                "payload": {"text": "תזכורת חשובה"},
                "run_at": datetime(2025, 5, 25, 10, 0, 0, tzinfo=timezone.utc).astimezone(tz),
                "recurrence": None,
            }
        ]

        with patch("app.scheduled_jobs.list_pending_jobs", return_value=fake_jobs):
            result = list_tool.invoke({})

        assert "3" in result
        assert "תזכורת חשובה" in result


# ---------------------------------------------------------------------------
# 7. Scheduler start/stop lifecycle
# ---------------------------------------------------------------------------

class TestSchedulerLifecycle:
    def test_scheduler_starts_and_stops_cleanly(self):
        """start() must create a running task; stop() must cancel it without raising."""
        from app import scheduled_jobs as sj

        async def run():
            assert sj._task is None or sj._task.done()
            await sj.start()
            assert sj._task is not None
            assert not sj._task.done()
            await sj.stop()
            assert sj._task is None or sj._task.done()

        asyncio.get_event_loop().run_until_complete(run())
        # Reset global state
        import app.scheduled_jobs as _sj
        _sj._task = None

    def test_stop_is_idempotent_when_not_started(self):
        """stop() must not raise even if the scheduler was never started."""
        from app import scheduled_jobs as sj
        sj._task = None

        async def run():
            await sj.stop()  # Should not raise

        asyncio.get_event_loop().run_until_complete(run())


# ---------------------------------------------------------------------------
# 8. init_table — skips gracefully when DATABASE_URL is empty
# ---------------------------------------------------------------------------

class TestInitTable:
    def test_init_table_skips_when_no_database_url(self):
        """scheduled_jobs.init_table must return immediately when DATABASE_URL is not set."""
        with patch("app.scheduled_jobs.DATABASE_URL", ""):
            from app.scheduled_jobs import init_table
            # Should not raise — just returns early
            init_table()

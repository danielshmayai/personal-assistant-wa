"""
Tests for the Garmin Connect integration — token persistence, login/MFA, daily
wellness sync, watch-activity import/merge, exercise-to-Garmin-category mapping,
structured workout push, and the periodic auto-sync proactive flow.

Rules:
- No real DB connections (psycopg2 is mocked throughout).
- No real Garmin Connect / network calls — `garminconnect` is stubbed in
  sys.modules with real Exception subclasses (needed for isinstance checks) and
  a configurable fake `Garmin` class; `app.garmin.client.call` is monkeypatched
  directly wherever sync/push logic is under test.
- Tests are fast and deterministic.
"""

from __future__ import annotations

import sys
import types
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# sys.path / isolation helpers (mirrors test_nutrition.py conventions)
# ---------------------------------------------------------------------------

def _add_backend_path():
    import os
    backend = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
    if backend not in sys.path:
        sys.path.insert(0, backend)


_add_backend_path()


@pytest.fixture(autouse=True)
def _isolate_modules():
    """Clean app.* modules between tests so patches/module-level caches
    (_client_cache, _pending_mfa, _daily_rec_cache) don't leak between tests."""
    _clean()
    yield
    _clean()


def _clean():
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def _run(coro):
    """Drive a coroutine to completion — this codebase doesn't use pytest-asyncio."""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# garminconnect stub — real Exception subclasses so isinstance() checks work,
# a configurable Garmin class so login/MFA flows can be scripted per-test.
# ---------------------------------------------------------------------------

class _StubGarminConnectConnectionError(Exception):
    pass


class _StubGarminConnectTooManyRequestsError(Exception):
    pass


class _StubGarminConnectAuthenticationError(Exception):
    pass


def _garmin_stub_modules(garmin_cls=None):
    """Return a sys.modules patch dict providing a fake 'garminconnect' package."""
    exceptions_mod = types.ModuleType("garminconnect.exceptions")
    exceptions_mod.GarminConnectConnectionError = _StubGarminConnectConnectionError
    exceptions_mod.GarminConnectTooManyRequestsError = _StubGarminConnectTooManyRequestsError
    exceptions_mod.GarminConnectAuthenticationError = _StubGarminConnectAuthenticationError

    garminconnect_mod = types.ModuleType("garminconnect")
    garminconnect_mod.Garmin = garmin_cls if garmin_cls is not None else MagicMock()
    garminconnect_mod.exceptions = exceptions_mod

    return {"garminconnect": garminconnect_mod, "garminconnect.exceptions": exceptions_mod}


def _mock_conn_fetchone(row):
    """A mock psycopg2 connection whose cursor().fetchone() returns *row*."""
    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_cursor.fetchone = MagicMock(return_value=row)
    fake_cursor.fetchall = MagicMock(return_value=[])

    fake_conn = MagicMock()
    fake_conn.__enter__ = MagicMock(return_value=fake_conn)
    fake_conn.__exit__ = MagicMock(return_value=False)
    fake_conn.cursor = MagicMock(return_value=fake_cursor)
    fake_conn.close = MagicMock()
    return fake_conn


def _mock_conn_fetchall(rows):
    """A mock psycopg2 connection whose cursor().fetchall() returns *rows*."""
    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_cursor.fetchall = MagicMock(return_value=rows)
    fake_cursor.fetchone = MagicMock(return_value=None)

    fake_conn = MagicMock()
    fake_conn.__enter__ = MagicMock(return_value=fake_conn)
    fake_conn.__exit__ = MagicMock(return_value=False)
    fake_conn.cursor = MagicMock(return_value=fake_cursor)
    fake_conn.close = MagicMock()
    return fake_conn


_IDENTITY_CRYPTO = {
    "encrypt": lambda v: v,
    "decrypt": lambda v: v,
}


# ---------------------------------------------------------------------------
# 1. Pure numeric helpers — _int_or_none / _float_or_none (sync.py)
# ---------------------------------------------------------------------------

class TestNumericHelpers:
    def test_int_or_none_rounds_floats(self):
        from app.garmin.sync import _int_or_none
        assert _int_or_none(3.6) == 4
        assert _int_or_none(3.4) == 3

    def test_int_or_none_none_passthrough(self):
        from app.garmin.sync import _int_or_none
        assert _int_or_none(None) is None

    def test_int_or_none_garbage_returns_none(self):
        from app.garmin.sync import _int_or_none
        assert _int_or_none("not-a-number") is None

    def test_float_or_none_rounds_to_two_decimals(self):
        from app.garmin.sync import _float_or_none
        assert _float_or_none(3.14159) == 3.14

    def test_float_or_none_none_passthrough(self):
        from app.garmin.sync import _float_or_none
        assert _float_or_none(None) is None


# ---------------------------------------------------------------------------
# 2. _extract_activity_metrics — running / cycling / strength shapes
# ---------------------------------------------------------------------------

class TestExtractActivityMetrics:
    def test_cycling_activity_gets_speed_not_pace(self):
        from app.garmin.sync import _extract_activity_metrics
        act = {
            "activityId": 999, "averageHR": 128, "maxHR": 155, "calories": 850,
            "distance": 32000, "elevationGain": 210, "averageSpeed": 4.37,
            "aerobicTrainingEffect": 3.2, "anaerobicTrainingEffect": 0.8,
        }
        m = _extract_activity_metrics(act, "cycling", 122.0)
        assert m["garmin_activity_id"] == "999"
        assert m["hr_avg"] == 128 and m["hr_max"] == 155
        assert m["distance_km"] == 32.0
        assert m["avg_speed_kmh"] == pytest.approx(15.73, abs=0.01)
        assert "pace_min_km" not in m

    def test_running_activity_gets_pace_not_speed(self):
        from app.garmin.sync import _extract_activity_metrics
        act = {"activityId": 111, "averageHR": 150, "maxHR": 172, "calories": 400,
               "distance": 5000, "steps": 6200}
        m = _extract_activity_metrics(act, "running", 28.0)
        assert m["distance_km"] == 5.0
        assert m["pace_min_km"] == pytest.approx(5.6, abs=0.01)
        assert "avg_speed_kmh" not in m
        assert m["steps"] == 6200

    def test_strength_activity_has_no_distance_fields(self):
        from app.garmin.sync import _extract_activity_metrics
        act = {"activityId": 222, "averageHR": 110, "calories": 300}
        m = _extract_activity_metrics(act, "strength_training", 45.0)
        assert m["hr_avg"] == 110
        assert "distance_km" not in m
        assert "pace_min_km" not in m
        assert "avg_speed_kmh" not in m

    def test_missing_fields_are_omitted_not_none(self):
        """None-valued fields must be dropped so they don't clobber merged DB columns."""
        from app.garmin.sync import _extract_activity_metrics
        m = _extract_activity_metrics({"activityId": 1}, "walking", 10.0)
        assert "hr_avg" not in m
        assert "calories" not in m
        assert m["garmin_activity_id"] == "1"


# ---------------------------------------------------------------------------
# 3. Token persistence — save/load/delete/is_connected (DB + crypto mocked)
# ---------------------------------------------------------------------------

class TestTokenPersistence:
    def test_save_token_blob_encrypts_and_upserts(self):
        calls = {}

        def fake_encrypt(v):
            calls["encrypted"] = v
            return f"ENC:{v}"

        fake_conn = _mock_conn_fetchone(None)
        with (
            patch("app.garmin.client.crypto.encrypt", side_effect=fake_encrypt),
            patch("psycopg2.connect", return_value=fake_conn),
        ):
            from app.garmin.client import save_token_blob
            save_token_blob("raw-token-blob")

        assert calls["encrypted"] == "raw-token-blob"
        cur = fake_conn.cursor.return_value
        args = cur.execute.call_args[0][1]
        assert args[0] == "ENC:raw-token-blob"
        fake_conn.commit.assert_called_once()

    def test_load_token_blob_decrypts_row(self):
        fake_conn = _mock_conn_fetchone(("ENC:abc",))
        with (
            patch("app.garmin.client.crypto.decrypt", side_effect=lambda v: v.replace("ENC:", "")),
            patch("psycopg2.connect", return_value=fake_conn),
        ):
            from app.garmin.client import load_token_blob
            result = load_token_blob()
        assert result == "abc"

    def test_load_token_blob_returns_none_when_absent(self):
        fake_conn = _mock_conn_fetchone(None)
        with patch("psycopg2.connect", return_value=fake_conn):
            from app.garmin.client import load_token_blob
            assert load_token_blob() is None

    def test_is_connected_true_when_token_present(self):
        fake_conn = _mock_conn_fetchone(("some-blob",))
        with patch.multiple("app.crypto", **_IDENTITY_CRYPTO), \
             patch("psycopg2.connect", return_value=fake_conn):
            from app.garmin.client import is_connected
            assert is_connected() is True

    def test_is_connected_false_when_absent(self):
        fake_conn = _mock_conn_fetchone(None)
        with patch("psycopg2.connect", return_value=fake_conn):
            from app.garmin.client import is_connected
            assert is_connected() is False

    def test_is_connected_false_on_db_error(self):
        with patch("psycopg2.connect", side_effect=RuntimeError("no db")):
            from app.garmin.client import is_connected
            assert is_connected() is False


# ---------------------------------------------------------------------------
# 4. get_client / invalidate_client — cache + GarminNotConnected
# ---------------------------------------------------------------------------

class TestGetClient:
    def test_raises_not_connected_when_no_token(self):
        fake_conn = _mock_conn_fetchone(None)
        with patch("psycopg2.connect", return_value=fake_conn):
            from app.garmin.client import get_client, GarminNotConnected
            with pytest.raises(GarminNotConnected):
                get_client()

    def test_resumes_session_from_stored_token(self):
        fake_conn = _mock_conn_fetchone(("token-blob",))
        fake_instance = MagicMock()
        fake_garmin_cls = MagicMock(return_value=fake_instance)

        with (
            patch.dict(sys.modules, _garmin_stub_modules(fake_garmin_cls)),
            patch.multiple("app.crypto", **_IDENTITY_CRYPTO),
            patch("psycopg2.connect", return_value=fake_conn),
        ):
            from app.garmin.client import get_client
            g = get_client()

        fake_instance.login.assert_called_once_with("token-blob")
        assert g is fake_instance

    def test_get_client_caches_across_calls(self):
        fake_conn = _mock_conn_fetchone(("token-blob",))
        fake_garmin_cls = MagicMock(return_value=MagicMock())

        with (
            patch.dict(sys.modules, _garmin_stub_modules(fake_garmin_cls)),
            patch.multiple("app.crypto", **_IDENTITY_CRYPTO),
            patch("psycopg2.connect", return_value=fake_conn),
        ):
            from app.garmin.client import get_client
            g1 = get_client()
            g2 = get_client()

        assert g1 is g2
        fake_garmin_cls.assert_called_once()  # only constructed once — second call hit cache

    def test_resume_failure_raises_not_connected(self):
        fake_conn = _mock_conn_fetchone(("stale-blob",))
        fake_instance = MagicMock()
        fake_instance.login.side_effect = RuntimeError("token expired")
        fake_garmin_cls = MagicMock(return_value=fake_instance)

        with (
            patch.dict(sys.modules, _garmin_stub_modules(fake_garmin_cls)),
            patch.multiple("app.crypto", **_IDENTITY_CRYPTO),
            patch("psycopg2.connect", return_value=fake_conn),
        ):
            from app.garmin.client import get_client, GarminNotConnected
            with pytest.raises(GarminNotConnected):
                get_client()

    def test_invalidate_client_forces_relogin(self):
        fake_conn = _mock_conn_fetchone(("token-blob",))
        fake_garmin_cls = MagicMock(side_effect=lambda: MagicMock())

        with (
            patch.dict(sys.modules, _garmin_stub_modules(fake_garmin_cls)),
            patch.multiple("app.crypto", **_IDENTITY_CRYPTO),
            patch("psycopg2.connect", return_value=fake_conn),
        ):
            from app.garmin.client import get_client, invalidate_client
            g1 = get_client()
            invalidate_client()
            g2 = get_client()

        assert g1 is not g2
        assert fake_garmin_cls.call_count == 2


# ---------------------------------------------------------------------------
# 5. begin_login / finish_mfa — direct success, MFA branch, error mapping
# ---------------------------------------------------------------------------

class TestBeginLoginAndMfa:
    def test_direct_login_success_saves_token(self):
        fake_instance = MagicMock()
        fake_instance.login.return_value = (None, None)
        fake_instance.client.dumps.return_value = "fresh-token-blob"
        fake_garmin_cls = MagicMock(return_value=fake_instance)

        saved = {}
        with patch.dict(sys.modules, _garmin_stub_modules(fake_garmin_cls)):
            with patch("app.garmin.client.save_token_blob", side_effect=lambda b: saved.setdefault("blob", b)):
                from app.garmin.client import begin_login
                result = begin_login("user@example.com", "pw")

        assert result == {"connected": True}
        assert saved["blob"] == "fresh-token-blob"
        fake_garmin_cls.assert_called_once_with(email="user@example.com", password="pw", return_on_mfa=True)

    def test_login_needs_mfa_stashes_pending_state(self):
        fake_instance = MagicMock()
        fake_instance.login.return_value = ("needs_mfa", {"some": "state"})
        fake_garmin_cls = MagicMock(return_value=fake_instance)

        with patch.dict(sys.modules, _garmin_stub_modules(fake_garmin_cls)):
            from app.garmin.client import begin_login
            result = begin_login("user@example.com", "pw")

        assert result == {"mfa_required": True}

    def test_finish_mfa_without_pending_raises(self):
        from app.garmin.client import finish_mfa, GarminNotConnected
        with pytest.raises(GarminNotConnected):
            finish_mfa("123456")

    def test_finish_mfa_completes_pending_login(self):
        fake_instance = MagicMock()
        fake_instance.login.return_value = ("needs_mfa", {"some": "state"})
        fake_instance.client.dumps.return_value = "mfa-token-blob"
        fake_garmin_cls = MagicMock(return_value=fake_instance)

        saved = {}
        with patch.dict(sys.modules, _garmin_stub_modules(fake_garmin_cls)):
            with patch("app.garmin.client.save_token_blob", side_effect=lambda b: saved.setdefault("blob", b)):
                from app.garmin.client import begin_login, finish_mfa
                begin_login("user@example.com", "pw")
                result = finish_mfa("  654321  ")

        assert result == {"connected": True}
        assert saved["blob"] == "mfa-token-blob"
        fake_instance.resume_login.assert_called_once_with({"some": "state"}, "654321")

    def test_begin_login_wraps_auth_error(self):
        exc_stub = _garmin_stub_modules()
        auth_error_cls = exc_stub["garminconnect.exceptions"].GarminConnectAuthenticationError
        fake_instance = MagicMock()
        fake_instance.login.side_effect = auth_error_cls("bad password")
        fake_garmin_cls = MagicMock(return_value=fake_instance)
        exc_stub["garminconnect"].Garmin = fake_garmin_cls

        with patch.dict(sys.modules, exc_stub):
            from app.garmin.client import begin_login
            with pytest.raises(RuntimeError, match="rejected the credentials"):
                begin_login("user@example.com", "wrong-pw")


class TestFriendlyLoginError:
    def test_too_many_requests_maps_to_rate_limit_message(self):
        stub = _garmin_stub_modules()
        exc = stub["garminconnect.exceptions"].GarminConnectTooManyRequestsError("429")
        with patch.dict(sys.modules, stub):
            from app.garmin.client import _friendly_login_error
            assert "rate-limited" in _friendly_login_error(exc)

    def test_auth_error_maps_to_credentials_message(self):
        stub = _garmin_stub_modules()
        exc = stub["garminconnect.exceptions"].GarminConnectAuthenticationError("nope")
        with patch.dict(sys.modules, stub):
            from app.garmin.client import _friendly_login_error
            msg = _friendly_login_error(exc)
            assert "rejected the credentials" in msg and "nope" in msg

    def test_generic_exception_passes_through(self):
        stub = _garmin_stub_modules()
        with patch.dict(sys.modules, stub):
            from app.garmin.client import _friendly_login_error
            assert _friendly_login_error(ConnectionError("network down")) == "network down"


class TestDisconnect:
    def test_disconnect_clears_token_and_cache(self):
        fake_conn = _mock_conn_fetchone(None)
        with patch("psycopg2.connect", return_value=fake_conn):
            from app.garmin.client import disconnect, is_connected
            disconnect()
            assert is_connected() is False
        fake_conn.cursor.return_value.execute.assert_any_call("DELETE FROM garmin_tokens WHERE id = 1")


# ---------------------------------------------------------------------------
# 6. sync_wellness — staleness cache + upsert wiring
# ---------------------------------------------------------------------------

class TestSyncWellness:
    def test_returns_cached_row_within_ttl(self):
        """A fresh (synced <30min ago) cached row must short-circuit — no gclient.call."""
        from app import garmin as _  # noqa: F401 (ensure package importable)
        recent_sync = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat()
        cached_row = {"date": "2026-07-04", "sleep_score": 80, "synced_at": recent_sync}

        call_mock = AsyncMock()
        with patch("app.garmin.sync.get_wellness", return_value=cached_row), \
             patch("app.garmin.sync._user_today", return_value=date(2026, 7, 4)), \
             patch("app.garmin.client.call", call_mock):
            from app.garmin.sync import sync_wellness
            result = _run(sync_wellness())

        assert result == cached_row
        call_mock.assert_not_called()

    def test_force_bypasses_cache_and_refetches(self):
        stale_row = {"date": "2026-07-04", "sleep_score": 70,
                     "synced_at": (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()}
        fresh_row = {"date": "2026-07-04", "sleep_score": 90, "synced_at": datetime.now().astimezone().isoformat()}

        async def fake_call(fn_name, *a, **kw):
            if fn_name == "get_stats":
                return {"totalSteps": 5000, "restingHeartRate": 50, "bodyBatteryHighestValue": 80,
                         "bodyBatteryLowestValue": 20, "bodyBatteryMostRecentValue": 60, "averageStressLevel": 30}
            if fn_name == "get_sleep_data":
                return {"dailySleepDTO": {"sleepScores": {"overall": {"value": 90}}, "sleepTimeSeconds": 25200}}
            raise AssertionError(f"unexpected call: {fn_name}")

        get_wellness_calls = [stale_row, fresh_row]
        with patch("app.garmin.sync.get_wellness", side_effect=lambda d: get_wellness_calls.pop(0)), \
             patch("app.garmin.sync._user_today", return_value=date(2026, 7, 4)), \
             patch("app.garmin.sync._upsert_wellness") as upsert_mock, \
             patch("app.garmin.client.call", fake_call):
            from app.garmin.sync import sync_wellness
            result = _run(sync_wellness(force=True))

        upsert_mock.assert_called_once()
        vals = upsert_mock.call_args[0][1]
        assert vals["sleep_score"] == 90
        assert vals["resting_hr"] == 50
        assert result == fresh_row

    def test_garmin_not_connected_propagates(self):
        from app.garmin.client import GarminNotConnected

        async def raising_call(*a, **kw):
            raise GarminNotConnected("session expired")

        with patch("app.garmin.sync.get_wellness", return_value=None), \
             patch("app.garmin.sync._user_today", return_value=date(2026, 7, 4)), \
             patch("app.garmin.client.call", raising_call):
            from app.garmin.sync import sync_wellness
            with pytest.raises(GarminNotConnected):
                _run(sync_wellness())


# ---------------------------------------------------------------------------
# 7. sync_activities — dedupe / merge / insert / evaluate
# ---------------------------------------------------------------------------

class TestSyncActivities:
    def _base_activity(self, **overrides):
        act = {
            "activityId": 12345,
            "activityName": "Cycling",
            "activityType": {"typeKey": "cycling"},
            "startTimeLocal": "2026-07-04 08:00:00",
            "duration": 7200,  # 120 min
            "averageHR": 128,
            "calories": 850,
            "distance": 32000,
        }
        act.update(overrides)
        return act

    def test_known_activity_id_is_skipped(self):
        act = self._base_activity(activityId=999)

        async def fake_call(fn_name, *a, **kw):
            assert fn_name == "get_activities_by_date"
            return [act]

        with patch("app.garmin.sync._user_today", return_value=date(2026, 7, 4)), \
             patch("app.garmin.sync._existing_garmin_ids", return_value={"999"}), \
             patch("app.garmin.client.call", fake_call):
            from app.garmin.sync import sync_activities
            result = _run(sync_activities())

        assert result == {"imported": 0, "merged": 0, "imported_workouts": []}

    def test_new_activity_without_merge_candidate_is_inserted_and_evaluated(self):
        act = self._base_activity(activityId=555)

        async def fake_call(fn_name, *a, **kw):
            return [act]

        insert_calls = []
        eval_calls = []

        def fake_insert_workout(*args, **kwargs):
            insert_calls.append(args)
            return 777

        async def fake_evaluate_workout(parsed, chat_id):
            eval_calls.append(parsed)
            return {"ai_summary": "אימון סבולת טוב.", "ai_next_rec": {"targets": []}}

        with patch("app.garmin.sync._user_today", return_value=date(2026, 7, 4)), \
             patch("app.garmin.sync._existing_garmin_ids", return_value=set()), \
             patch("app.garmin.sync._find_merge_candidate", return_value=None), \
             patch("app.garmin.client.call", fake_call), \
             patch("app.fitness.insert_workout", side_effect=fake_insert_workout), \
             patch("app.fitness.evaluate_workout", side_effect=fake_evaluate_workout), \
             patch("app.fitness.update_workout_ai") as update_ai_mock:
            from app.garmin.sync import sync_activities
            result = _run(sync_activities())

        assert result["imported"] == 1
        assert result["merged"] == 0
        assert len(insert_calls) == 1
        assert insert_calls[0][0] == "default"
        assert insert_calls[0][7] == "garmin"  # source positional arg
        assert insert_calls[0][8] == date(2026, 7, 4)  # log_date positional arg
        assert len(eval_calls) == 1
        update_ai_mock.assert_called_once_with(777, "אימון סבולת טוב.", {"targets": []})

        workouts = result["imported_workouts"]
        assert len(workouts) == 1
        assert workouts[0]["id"] == 777
        assert workouts[0]["parsed"]["title"] == "Cycling"
        assert workouts[0]["parsed"]["metrics"]["distance_km"] == 32.0
        assert workouts[0]["evaluation"]["ai_summary"] == "אימון סבולת טוב."

    def test_new_activity_with_merge_candidate_merges_instead_of_inserting(self):
        act = self._base_activity(activityId=321)

        async def fake_call(fn_name, *a, **kw):
            return [act]

        merge_calls = []

        with patch("app.garmin.sync._user_today", return_value=date(2026, 7, 4)), \
             patch("app.garmin.sync._existing_garmin_ids", return_value=set()), \
             patch("app.garmin.sync._find_merge_candidate", return_value=42), \
             patch("app.garmin.client.call", fake_call), \
             patch("app.fitness.merge_garmin_metrics", side_effect=lambda wid, m: merge_calls.append((wid, m))), \
             patch("app.fitness.get_workout", return_value={
                 "title": "Cycling", "workout_type": "cardio", "duration_min": 120,
                 "avg_rpe": 0, "exercises": [], "metrics": {},
             }), \
             patch("app.fitness.insert_workout") as insert_mock, \
             patch("app.fitness.evaluate_workout", new=AsyncMock(return_value={"ai_summary": "", "ai_next_rec": {}})), \
             patch("app.fitness.update_workout_ai"):
            from app.garmin.sync import sync_activities
            result = _run(sync_activities())

        assert result["merged"] == 1
        assert result["imported"] == 0
        insert_mock.assert_not_called()
        assert len(merge_calls) == 1
        assert merge_calls[0][0] == 42
        assert merge_calls[0][1]["garmin_activity_id"] == "321"
        # Merged activities aren't surfaced as "newly imported" notifications.
        assert result["imported_workouts"] == []

    def test_garmin_api_failure_returns_zeroed_result(self):
        async def raising_call(*a, **kw):
            raise ConnectionError("garmin down")

        with patch("app.garmin.sync._user_today", return_value=date(2026, 7, 4)), \
             patch("app.garmin.client.call", raising_call):
            from app.garmin.sync import sync_activities
            result = _run(sync_activities())

        assert result == {"imported": 0, "merged": 0, "imported_workouts": []}

    def test_one_bad_activity_does_not_abort_the_whole_sync(self):
        """A malformed activity (unparsable startTimeLocal) is skipped; good ones still land."""
        bad = self._base_activity(activityId=1, startTimeLocal="not-a-date")
        good = self._base_activity(activityId=2)

        async def fake_call(fn_name, *a, **kw):
            return [bad, good]

        with patch("app.garmin.sync._user_today", return_value=date(2026, 7, 4)), \
             patch("app.garmin.sync._existing_garmin_ids", return_value=set()), \
             patch("app.garmin.sync._find_merge_candidate", return_value=None), \
             patch("app.garmin.client.call", fake_call), \
             patch("app.fitness.insert_workout", return_value=1), \
             patch("app.fitness.evaluate_workout", new=AsyncMock(return_value={"ai_summary": "", "ai_next_rec": {}})), \
             patch("app.fitness.update_workout_ai"):
            from app.garmin.sync import sync_activities
            result = _run(sync_activities())

        assert result["imported"] == 1


# ---------------------------------------------------------------------------
# 8. push_hydration — connected/ml guards
# ---------------------------------------------------------------------------

class TestPushHydration:
    def test_skips_when_not_connected(self):
        call_mock = AsyncMock()
        with patch("app.garmin.client.is_connected", return_value=False), \
             patch("app.garmin.client.call", call_mock):
            from app.garmin.sync import push_hydration
            _run(push_hydration(500))
        call_mock.assert_not_called()

    def test_skips_when_ml_not_positive(self):
        call_mock = AsyncMock()
        with patch("app.garmin.client.is_connected", return_value=True), \
             patch("app.garmin.client.call", call_mock):
            from app.garmin.sync import push_hydration
            _run(push_hydration(0))
        call_mock.assert_not_called()

    def test_pushes_hydration_when_connected(self):
        call_mock = AsyncMock()
        with patch("app.garmin.client.is_connected", return_value=True), \
             patch("app.garmin.sync._user_today", return_value=date(2026, 7, 4)), \
             patch("app.garmin.client.call", call_mock):
            from app.garmin.sync import push_hydration
            _run(push_hydration(250))
        call_mock.assert_called_once_with("add_hydration_data", value_in_ml=250, cdate="2026-07-04")

    def test_network_failure_is_swallowed(self):
        async def raising_call(*a, **kw):
            raise ConnectionError("garmin down")

        with patch("app.garmin.client.is_connected", return_value=True), \
             patch("app.garmin.sync._user_today", return_value=date(2026, 7, 4)), \
             patch("app.garmin.client.call", raising_call):
            from app.garmin.sync import push_hydration
            _run(push_hydration(250))  # must not raise


# ---------------------------------------------------------------------------
# 9. workout_push — exercise-name mapping
# ---------------------------------------------------------------------------

class TestExerciseMapping:
    @pytest.mark.parametrize("name,expected_category,expected_ex_name", [
        ("לג פרס במכונה (Leg Press) (Machine)", "SQUAT", "LEG_PRESS"),
        ("כפיפת ברכיים במכונה (Hamstring Curl Machine)", "LEG_CURL", None),
        ("לחיצת חזה במכונה (Machine Chest Press)", "BENCH_PRESS", None),
        ("Face Pull (Cable)", "ROW", None),
        ("פלאנק (Plank)", "PLANK", None),
        ("משיכת פולי עליון (Lat Pulldown)", "PULL_UP", None),
        ("Romanian Deadlift", "DEADLIFT", "ROMANIAN_DEADLIFT"),
        ("Overhead Shoulder Press", "SHOULDER_PRESS", None),
        ("תרגיל לא מוכר כלשהו", None, None),
    ])
    def test_match_exercise(self, name, expected_category, expected_ex_name):
        from app.garmin.workout_push import _match_exercise
        category, ex_name = _match_exercise(name)
        assert category == expected_category
        assert ex_name == expected_ex_name

    def test_english_part_extracts_from_parentheses(self):
        from app.garmin.workout_push import _english_part
        assert "Leg Press" in _english_part("לג פרס (Leg Press) (Machine)")

    def test_english_part_handles_pure_hebrew(self):
        from app.garmin.workout_push import _english_part
        # No latin characters at all — result should just be blank/whitespace, not crash.
        result = _english_part("תרגיל בעברית בלבד")
        assert result.strip() == ""


# ---------------------------------------------------------------------------
# 10. workout_push.build_payload — payload structure
# ---------------------------------------------------------------------------

class TestBuildPayload:
    def test_rep_based_exercise_uses_reps_end_condition(self):
        from app.garmin.workout_push import build_payload
        plan = {"title": "אימון כוח", "rationale": "test",
                "exercises": [{"name": "Leg Press", "sets": 3, "reps": 10, "weight_kg": 75.5}]}
        payload = build_payload(plan)
        assert payload["sportType"]["sportTypeKey"] == "strength_training"
        steps = payload["workoutSegments"][0]["workoutSteps"]
        assert len(steps) == 1
        assert steps[0]["numberOfIterations"] == 3
        work_step = steps[0]["workoutSteps"][0]
        assert work_step["endCondition"]["conditionTypeKey"] == "reps"
        assert work_step["endConditionValue"] == 10.0
        assert work_step["category"] == "SQUAT"
        assert work_step["exerciseName"] == "LEG_PRESS"
        assert work_step["weightValue"] == 75500.0
        rest_step = steps[0]["workoutSteps"][1]
        assert rest_step["stepType"]["stepTypeKey"] == "rest"

    def test_timed_exercise_uses_time_end_condition(self):
        from app.garmin.workout_push import build_payload
        plan = {"exercises": [{"name": "Plank", "sets": 2, "reps": 0, "duration_sec": 45}]}
        payload = build_payload(plan)
        work_step = payload["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]
        assert work_step["endCondition"]["conditionTypeKey"] == "time"
        assert work_step["endConditionValue"] == 45.0

    def test_unmatched_exercise_still_produces_a_step(self):
        from app.garmin.workout_push import build_payload
        plan = {"exercises": [{"name": "תרגיל עלום", "sets": 3, "reps": 8}]}
        payload = build_payload(plan)
        work_step = payload["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]
        assert "category" not in work_step
        assert work_step["endConditionValue"] == 8.0

    def test_multiple_exercises_get_distinct_child_ids(self):
        from app.garmin.workout_push import build_payload
        plan = {"exercises": [
            {"name": "Squat", "sets": 3, "reps": 8},
            {"name": "Bench Press", "sets": 3, "reps": 8},
        ]}
        payload = build_payload(plan)
        steps = payload["workoutSegments"][0]["workoutSteps"]
        assert steps[0]["childStepId"] != steps[1]["childStepId"]


# ---------------------------------------------------------------------------
# 11. push_workout — empty plan, success, schedule failure resilience
# ---------------------------------------------------------------------------

class TestPushWorkout:
    def test_empty_exercises_raises_value_error(self):
        from app.garmin.workout_push import push_workout
        with pytest.raises(ValueError):
            _run(push_workout({"exercises": []}))

    def test_success_path_returns_workout_id_and_scheduled(self):
        plan = {"title": "אימון", "exercises": [{"name": "Squat", "sets": 3, "reps": 8}]}

        async def fake_call(fn_name, *args, **kwargs):
            if fn_name == "connectapi":
                return {"workoutId": "abc123"}
            if fn_name == "schedule_workout":
                return {}
            raise AssertionError(fn_name)

        with patch("app.garmin.client.call", fake_call), \
             patch("app.fitness._user_today", return_value=date(2026, 7, 4)):
            from app.garmin.workout_push import push_workout
            result = _run(push_workout(plan))

        assert result["workoutId"] == "abc123"
        assert result["scheduled"] is True
        assert result["matched"] == 1

    def test_missing_workout_id_raises_runtime_error(self):
        plan = {"exercises": [{"name": "Squat", "sets": 3, "reps": 8}]}

        async def fake_call(fn_name, *args, **kwargs):
            return {}  # no workoutId

        with patch("app.garmin.client.call", fake_call):
            from app.garmin.workout_push import push_workout
            with pytest.raises(RuntimeError):
                _run(push_workout(plan))

    def test_schedule_failure_does_not_fail_the_whole_push(self):
        plan = {"exercises": [{"name": "Squat", "sets": 3, "reps": 8}]}

        async def fake_call(fn_name, *args, **kwargs):
            if fn_name == "connectapi":
                return {"workoutId": "xyz"}
            raise ConnectionError("schedule endpoint down")

        with patch("app.garmin.client.call", fake_call), \
             patch("app.fitness._user_today", return_value=date(2026, 7, 4)):
            from app.garmin.workout_push import push_workout
            result = _run(push_workout(plan))

        assert result["workoutId"] == "xyz"
        assert result["scheduled"] is False


# ---------------------------------------------------------------------------
# 12. Garmin router — mounted on the FastAPI app
# ---------------------------------------------------------------------------

def _stub_missing_modules() -> dict:
    stubs = {}
    for mod_name in [
        "langchain_ollama", "langgraph", "langgraph.checkpoint",
        "langgraph.checkpoint.postgres", "langgraph.checkpoint.postgres.aio",
    ]:
        if mod_name not in sys.modules:
            stubs[mod_name] = MagicMock()
    return stubs


class TestGarminRouterMounted:
    def test_garmin_router_registered_on_app(self):
        with patch.dict(sys.modules, {**_stub_missing_modules(), **_garmin_stub_modules()}):
            with patch("app.memory.store._get_conn", side_effect=RuntimeError("no db")):
                from app.main import app

        routes = [r.path for r in app.routes if hasattr(r, "path")]
        for expected in [
            "/api/garmin/status", "/api/garmin/connect", "/api/garmin/mfa",
            "/api/garmin/disconnect", "/api/garmin/wellness", "/api/garmin/push-workout",
        ]:
            assert expected in routes, f"Expected {expected} in routes, got: {routes}"


# ---------------------------------------------------------------------------
# 13. Proactive flow — _notify_new_garmin_workouts builds log_workout-style text
# ---------------------------------------------------------------------------

class TestNotifyNewGarminWorkouts:
    def test_no_workouts_sends_nothing(self):
        insert_mock = MagicMock()
        with patch("app.scheduled_jobs.insert_job", insert_mock):
            from app.proactive.flows import _notify_new_garmin_workouts
            _run(_notify_new_garmin_workouts(["972501234@c.us"], {"imported_workouts": []}))
        insert_mock.assert_not_called()

    def test_sends_one_job_per_chat_per_workout_with_watch_tag(self):
        result = {
            "imported_workouts": [
                {
                    "parsed": {"title": "Cycling", "workout_type": "cardio", "duration_min": 122,
                               "avg_rpe": 0, "exercises": [], "metrics": {"distance_km": 32.0, "hr_avg": 128}},
                    "evaluation": {"ai_summary": "אימון סבולת טוב.", "ai_next_rec": {"targets": []}},
                }
            ]
        }
        insert_calls = []
        with patch("app.scheduled_jobs.insert_job", side_effect=lambda **kw: insert_calls.append(kw)), \
             patch("app.fitness.list_today", return_value={"total_duration_min": 122, "total_volume": 0}):
            from app.proactive.flows import _notify_new_garmin_workouts
            _run(_notify_new_garmin_workouts(["972501234@c.us"], result))

        assert len(insert_calls) == 1
        call = insert_calls[0]
        assert call["chat_id"] == "972501234@c.us"
        assert call["action_type"] == "send_message"
        text = call["payload"]["text"]
        assert "Cycling" in text and "⌚" in text
        assert "אימון סבולת טוב." in text

    def test_multiple_chat_ids_each_get_notified(self):
        result = {"imported_workouts": [{
            "parsed": {"title": "Run", "workout_type": "cardio", "duration_min": 30,
                       "avg_rpe": 0, "exercises": [], "metrics": {}},
            "evaluation": {"ai_summary": "", "ai_next_rec": {}},
        }]}
        insert_calls = []
        with patch("app.scheduled_jobs.insert_job", side_effect=lambda **kw: insert_calls.append(kw)), \
             patch("app.fitness.list_today", return_value={"total_duration_min": 30, "total_volume": 0}):
            from app.proactive.flows import _notify_new_garmin_workouts
            _run(_notify_new_garmin_workouts(["chat1", "chat2"], result))

        assert {c["chat_id"] for c in insert_calls} == {"chat1", "chat2"}

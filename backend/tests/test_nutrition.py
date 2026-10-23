"""
Tests for nutrition tracking — DB helpers, water logic, tools, and API wiring.

Rules:
- No real DB connections (psycopg2 is mocked throughout).
- No real LLM/Gemini calls (parse_meal_text / parse_meal_image are mocked).
- The web API lives in the product; only its route wiring is checked here.
- Tests are fast and deterministic.
"""

from __future__ import annotations

import sys
import asyncio
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# sys.path / isolation helpers
# ---------------------------------------------------------------------------

def _add_backend_path():
    """Ensure 'backend/' is on sys.path so `app.*` imports resolve."""
    import os
    backend = os.path.join(os.path.dirname(__file__), "..", )
    backend = os.path.normpath(backend)
    if backend not in sys.path:
        sys.path.insert(0, backend)


_add_backend_path()


@pytest.fixture(autouse=True)
def _isolate_modules():
    """Clean app.* modules between tests to avoid patch leakage."""
    _clean()
    yield
    _clean()


def _clean():
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


# ---------------------------------------------------------------------------
# 1. _nutrition_key — always returns "default"
# ---------------------------------------------------------------------------

class TestNutritionKey:
    def test_web_chat_id_returns_default(self):
        from app.nutrition import _nutrition_key
        assert _nutrition_key("web_abc123") == "default"

    def test_whatsapp_number_returns_default(self):
        from app.nutrition import _nutrition_key
        assert _nutrition_key("972501234@c.us") == "default"

    def test_already_default_returns_default(self):
        from app.nutrition import _nutrition_key
        assert _nutrition_key("default") == "default"

    def test_empty_string_returns_default(self):
        from app.nutrition import _nutrition_key
        assert _nutrition_key("") == "default"

    def test_arbitrary_string_returns_default(self):
        from app.nutrition import _nutrition_key
        assert _nutrition_key("some_other_chat_id_123") == "default"


# ---------------------------------------------------------------------------
# 2. list_today — mocked DB rows
# ---------------------------------------------------------------------------

def _make_fake_row(
    row_id: int,
    meal_description: str,
    protein: float,
    carbs: float,
    calories: float,
    micros: dict,
    source: str,
    created_at=None,
):
    """Build a dict that mimics a psycopg2 RealDictCursor row."""
    if created_at is None:
        created_at = datetime(2025, 5, 25, 10, 0, 0, tzinfo=timezone.utc)
    return {
        "id": row_id,
        "meal_description": meal_description,
        "protein": protein,
        "carbs": carbs,
        "calories": calories,
        "micros": micros,
        "source": source,
        "created_at": created_at,
    }


def _mock_conn_with_rows(rows: list[dict]):
    """Return a mock psycopg2 connection that yields *rows* from fetchall()."""
    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_cursor.fetchall = MagicMock(return_value=rows)

    fake_conn = MagicMock()
    fake_conn.__enter__ = MagicMock(return_value=fake_conn)
    fake_conn.__exit__ = MagicMock(return_value=False)
    fake_conn.cursor = MagicMock(return_value=fake_cursor)
    fake_conn.close = MagicMock()
    return fake_conn


class TestListToday:
    def test_empty_db_returns_zero_totals(self):
        """list_today with no rows → meals=[], totals all zero, water_ml=0."""
        with (
            patch("app.nutrition.DATABASE_URL", "postgresql://fake"),
            patch("psycopg2.connect", return_value=_mock_conn_with_rows([])),
        ):
            from app.nutrition import list_today
            result = list_today("default")

        assert result["meals"] == []
        assert result["totals"]["protein"] == 0.0
        assert result["totals"]["carbs"] == 0.0
        assert result["totals"]["calories"] == 0.0
        assert result["water_ml"] == 0
        assert result["water_target_ml"] == 2000

    def test_water_rows_excluded_from_meals(self):
        """Rows with source='water' must NOT appear in the meals list."""
        rows = [
            _make_fake_row(1, "עוף ואורז", 30, 40, 300, {}, "text"),
            _make_fake_row(2, "מים (500 מ\"ל)", 0, 0, 0, {"water_ml": 500}, "water"),
        ]
        with (
            patch("app.nutrition.DATABASE_URL", "postgresql://fake"),
            patch("psycopg2.connect", return_value=_mock_conn_with_rows(rows)),
        ):
            from app.nutrition import list_today
            result = list_today("default")

        assert len(result["meals"]) == 1
        assert result["meals"][0]["source"] == "text"
        assert result["meals"][0]["meal_description"] == "עוף ואורז"

    def test_water_ml_summed_from_water_rows(self):
        """water_ml in result must be the sum of all water_ml micros across water rows."""
        rows = [
            _make_fake_row(1, "מים (250 מ\"ל)", 0, 0, 0, {"water_ml": 250}, "water"),
            _make_fake_row(2, "מים (500 מ\"ל)", 0, 0, 0, {"water_ml": 500}, "water"),
            _make_fake_row(3, "מים (250 מ\"ל)", 0, 0, 0, {"water_ml": 250}, "water"),
        ]
        with (
            patch("app.nutrition.DATABASE_URL", "postgresql://fake"),
            patch("psycopg2.connect", return_value=_mock_conn_with_rows(rows)),
        ):
            from app.nutrition import list_today
            result = list_today("default")

        assert result["water_ml"] == 1000

    def test_meal_protein_summed_correctly(self):
        """protein total must sum only non-water rows."""
        rows = [
            _make_fake_row(1, "ביצה", 6, 0, 70, {}, "text"),
            _make_fake_row(2, "חזה עוף", 30, 0, 165, {}, "text"),
            _make_fake_row(3, "מים", 0, 0, 0, {"water_ml": 300}, "water"),
        ]
        with (
            patch("app.nutrition.DATABASE_URL", "postgresql://fake"),
            patch("psycopg2.connect", return_value=_mock_conn_with_rows(rows)),
        ):
            from app.nutrition import list_today
            result = list_today("default")

        assert result["totals"]["protein"] == 36.0
        assert result["totals"]["calories"] == 235.0
        assert result["water_ml"] == 300

    def test_water_ml_excluded_from_totals_micros(self):
        """water_ml must be popped from totals.micros and moved to top-level water_ml."""
        rows = [
            _make_fake_row(1, "מים", 0, 0, 0, {"water_ml": 750, "sodium_mg": 5}, "water"),
        ]
        with (
            patch("app.nutrition.DATABASE_URL", "postgresql://fake"),
            patch("psycopg2.connect", return_value=_mock_conn_with_rows(rows)),
        ):
            from app.nutrition import list_today
            result = list_today("default")

        assert "water_ml" not in result["totals"]["micros"]
        assert result["water_ml"] == 750

    def test_returns_empty_when_no_database_url(self):
        """When DATABASE_URL is empty, list_today returns a zeroed result without connecting."""
        with patch("app.nutrition.DATABASE_URL", ""):
            from app.nutrition import list_today
            result = list_today("default")

        assert result["meals"] == []
        assert result["totals"]["protein"] == 0.0


# ---------------------------------------------------------------------------
# 3. log_water — calls insert_log with correct args
# ---------------------------------------------------------------------------

class TestLogWater:
    def test_log_water_calls_insert_log_with_water_source(self):
        """log_water must delegate to insert_log with source='water' and water_ml micro."""
        insert_calls = []

        with patch("app.nutrition.DATABASE_URL", "postgresql://fake"):
            from app import nutrition as nutrition_mod

            original_insert = nutrition_mod.insert_log
            try:
                def recording_insert(chat_id, meal_description, protein, carbs, calories, micros, source="text", log_date=None):
                    insert_calls.append({
                        "chat_id": chat_id,
                        "meal_description": meal_description,
                        "protein": protein,
                        "carbs": carbs,
                        "calories": calories,
                        "micros": micros,
                        "source": source,
                        "log_date": log_date,
                    })
                    return 42

                nutrition_mod.insert_log = recording_insert
                nutrition_mod.log_water("test_chat", 500)
            finally:
                nutrition_mod.insert_log = original_insert

        assert len(insert_calls) == 1
        call = insert_calls[0]
        assert call["source"] == "water"
        assert call["micros"] == {"water_ml": 500}
        assert call["protein"] == 0
        assert call["carbs"] == 0
        assert call["calories"] == 0

    def test_log_water_enforces_minimum_1ml(self):
        """log_water must store at least 1 ml even when 0 is passed."""
        insert_calls = []

        with patch("app.nutrition.DATABASE_URL", "postgresql://fake"):
            from app import nutrition as nutrition_mod

            original_insert = nutrition_mod.insert_log
            try:
                def recording_insert(chat_id, meal_desc, protein, carbs, calories, micros, source="text", log_date=None):
                    insert_calls.append(micros)
                    return 1
                nutrition_mod.insert_log = recording_insert
                nutrition_mod.log_water("test_chat", 0)
            finally:
                nutrition_mod.insert_log = original_insert

        assert insert_calls[0]["water_ml"] == 1

    def test_log_water_uses_nutrition_key(self):
        """log_water must normalise chat_id through _nutrition_key (always 'default')."""
        insert_calls = []

        with patch("app.nutrition.DATABASE_URL", "postgresql://fake"):
            from app import nutrition as nutrition_mod

            original_insert = nutrition_mod.insert_log
            try:
                def recording_insert(chat_id, *args, **kwargs):
                    insert_calls.append(chat_id)
                    return 1
                nutrition_mod.insert_log = recording_insert
                nutrition_mod.log_water("972501234@c.us", 300)
            finally:
                nutrition_mod.insert_log = original_insert

        # _nutrition_key turns any chat_id into "default"
        assert insert_calls[0] == "default"


# ---------------------------------------------------------------------------
# 4. water_today helper
# ---------------------------------------------------------------------------

class TestWaterToday:
    def test_water_today_sums_from_list_today(self):
        """water_today must return the water_ml value from list_today."""
        fake_data = {
            "meals": [],
            "date": "2025-05-25",
            "protein_target": 110,
            "water_ml": 1250,
            "water_target_ml": 2000,
            "totals": {"protein": 0, "carbs": 0, "calories": 0, "micros": {"water_ml": 1250}},
        }
        from app import nutrition as nutrition_mod
        with patch.object(nutrition_mod, "list_today", return_value=fake_data):
            result = nutrition_mod.water_today("any_chat")
        assert result == 1250

    def test_water_today_returns_zero_when_no_water_logged(self):
        """water_today returns 0 when no water rows exist."""
        fake_data = {
            "meals": [],
            "date": "2025-05-25",
            "protein_target": 110,
            "water_ml": 0,
            "water_target_ml": 2000,
            "totals": {"protein": 0, "carbs": 0, "calories": 0, "micros": {}},
        }
        from app import nutrition as nutrition_mod
        with patch.object(nutrition_mod, "list_today", return_value=fake_data):
            result = nutrition_mod.water_today("any_chat")
        assert result == 0


# ---------------------------------------------------------------------------
# 5. get_nutrition_tools — tool registration
# ---------------------------------------------------------------------------

class TestNutritionTools:
    def test_get_nutrition_tools_returns_three_tools(self):
        """get_nutrition_tools must return exactly 3 tools."""
        from app.nutrition_tool import get_nutrition_tools
        tools = get_nutrition_tools("test-chat")
        assert len(tools) == 3

    def test_get_nutrition_tools_names(self):
        """get_nutrition_tools must return log_meal, log_water, nutrition_today."""
        from app.nutrition_tool import get_nutrition_tools
        tools = get_nutrition_tools("test-chat")
        names = {t.name for t in tools}
        assert names == {"log_meal", "log_water", "nutrition_today"}

    def test_nutrition_tools_are_bound_to_chat_id(self):
        """Different chat_ids must produce distinct tool instances."""
        from app.nutrition_tool import get_nutrition_tools
        tools_a = get_nutrition_tools("chat-a")
        tools_b = get_nutrition_tools("chat-b")
        # They should be separate objects (closures over different chat_id)
        assert tools_a is not tools_b


# ---------------------------------------------------------------------------
# 6. Nutrition API is served by the product (the web app's backend)
# ---------------------------------------------------------------------------

class TestNutritionRouterMounted:
    def test_nutrition_router_registered_on_product_app(self):
        """The web app's nutrition API lives in the product; the engine's
        legacy copy was retired with the old static web UI."""
        from product.main import app

        routes = [r.path for r in app.routes if hasattr(r, "path")]
        for expected in ("/api/nutrition/today", "/api/nutrition/log-water",
                         "/api/nutrition/log-text", "/api/nutrition/meal-suggestion"):
            assert expected in routes, f"Expected {expected} in routes, got: {routes}"

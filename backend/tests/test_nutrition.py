"""
Tests for nutrition tracking — DB helpers, water logic, and REST API endpoints.

Rules:
- No real DB connections (psycopg2 is mocked throughout).
- No real LLM/Gemini calls (parse_meal_text / parse_meal_image are mocked).
- FastAPI endpoints tested via httpx.AsyncClient with the app object.
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
# 6. REST API — nutrition endpoints (mocked nutrition module)
# ---------------------------------------------------------------------------

def _fake_list_today_data():
    return {
        "date": "2025-05-25",
        "protein_target": 110,
        "meals": [
            {
                "id": 1,
                "meal_description": "ביצה קשה",
                "protein": 6.0,
                "carbs": 0.5,
                "calories": 70.0,
                "micros": {},
                "source": "text",
                "created_at": "2025-05-25T08:00:00+00:00",
            }
        ],
        "water_ml": 500,
        "water_target_ml": 2000,
        "totals": {
            "protein": 6.0,
            "carbs": 0.5,
            "calories": 70.0,
            "micros": {},
        },
    }


_FAKE_TEST_TOKEN = "test-tok-abc"


def _get_auth_headers():
    return {"Authorization": f"Bearer {_FAKE_TEST_TOKEN}"}


def _stub_missing_modules() -> dict:
    """Return a sys.modules patch dict for packages absent in the test environment."""
    stubs = {}
    for mod_name in [
        "langchain_ollama",
        "langgraph",
        "langgraph.checkpoint",
        "langgraph.checkpoint.postgres",
        "langgraph.checkpoint.postgres.aio",
    ]:
        if mod_name not in sys.modules:
            stubs[mod_name] = MagicMock()
    return stubs


@pytest.fixture
def nutrition_app():
    """Return the FastAPI app with DB and external dependencies mocked.

    Several LangChain packages are not installed in the test environment, so we
    stub them in sys.modules before importing app.main.
    """
    with patch.dict(sys.modules, _stub_missing_modules()):
        with (
            patch("app.nutrition.DATABASE_URL", "postgresql://fake"),
            patch("app.config.TEST_TOKEN", _FAKE_TEST_TOKEN),
            patch("app.routers.nutrition.TEST_TOKEN", _FAKE_TEST_TOKEN),
            patch("app.memory.store._get_conn", side_effect=RuntimeError("no db")),
        ):
            from app.main import app
            yield app


@pytest.mark.asyncio
async def test_rest_log_water_returns_200(nutrition_app):
    """POST /api/nutrition/log-water returns 200 with today's data."""
    import httpx
    from app import nutrition as nutrition_mod

    with (
        patch.object(nutrition_mod, "log_water", return_value=1),
        patch.object(nutrition_mod, "list_today", return_value=_fake_list_today_data()),
    ):
        transport = httpx.ASGITransport(app=nutrition_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/nutrition/log-water?chat_id=default",
                json={"amount_ml": 500},
                headers=_get_auth_headers(),
            )

    assert response.status_code == 200
    body = response.json()
    assert "today" in body
    assert body["today"]["water_ml"] == 500


@pytest.mark.asyncio
async def test_rest_log_water_rejects_zero_amount(nutrition_app):
    """POST /api/nutrition/log-water returns 400 when amount_ml <= 0."""
    import httpx

    transport = httpx.ASGITransport(app=nutrition_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/nutrition/log-water?chat_id=default",
            json={"amount_ml": 0},
            headers=_get_auth_headers(),
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_rest_log_text_returns_200(nutrition_app):
    """POST /api/nutrition/log-text returns 200 with entry and today data."""
    import httpx
    from app import nutrition as nutrition_mod

    fake_parsed = {
        "meal_description": "חזה עוף",
        "protein": 30.0,
        "carbs": 0.0,
        "calories": 165.0,
        "micros": {},
    }

    with (
        patch.object(nutrition_mod, "parse_meal_text", new_callable=AsyncMock, return_value=fake_parsed),
        patch.object(nutrition_mod, "insert_log", return_value=2),
        patch.object(nutrition_mod, "list_today", return_value=_fake_list_today_data()),
    ):
        transport = httpx.ASGITransport(app=nutrition_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/nutrition/log-text?chat_id=default",
                json={"text": "חזה עוף 150 גרם"},
                headers=_get_auth_headers(),
            )

    assert response.status_code == 200
    body = response.json()
    assert "id" in body
    assert "entry" in body
    assert "today" in body
    assert body["entry"]["meal_description"] == "חזה עוף"


@pytest.mark.asyncio
async def test_rest_log_text_returns_400_for_empty_text(nutrition_app):
    """POST /api/nutrition/log-text returns 400 when text is empty."""
    import httpx

    transport = httpx.ASGITransport(app=nutrition_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/nutrition/log-text?chat_id=default",
            json={"text": ""},
            headers=_get_auth_headers(),
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_rest_today_returns_expected_shape(nutrition_app):
    """GET /api/nutrition/today returns the correct data shape."""
    import httpx
    from app import nutrition as nutrition_mod

    with patch.object(nutrition_mod, "list_today", return_value=_fake_list_today_data()):
        transport = httpx.ASGITransport(app=nutrition_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/nutrition/today?chat_id=default",
                headers=_get_auth_headers(),
            )

    assert response.status_code == 200
    body = response.json()
    assert "meals" in body
    assert "totals" in body
    assert "water_ml" in body
    assert "water_target_ml" in body
    assert "protein_target" in body
    assert isinstance(body["meals"], list)


@pytest.mark.asyncio
async def test_rest_delete_returns_404_when_not_found(nutrition_app):
    """DELETE /api/nutrition/{id} returns 404 when the entry does not exist."""
    import httpx
    from app import nutrition as nutrition_mod

    with patch.object(nutrition_mod, "delete_log", return_value=False):
        transport = httpx.ASGITransport(app=nutrition_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(
                "/api/nutrition/9999?chat_id=default",
                headers=_get_auth_headers(),
            )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rest_delete_returns_200_when_found(nutrition_app):
    """DELETE /api/nutrition/{id} returns 200 when the entry is successfully deleted."""
    import httpx
    from app import nutrition as nutrition_mod

    with patch.object(nutrition_mod, "delete_log", return_value=True):
        transport = httpx.ASGITransport(app=nutrition_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(
                "/api/nutrition/1?chat_id=default",
                headers=_get_auth_headers(),
            )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_rest_nutrition_requires_auth(nutrition_app):
    """Nutrition endpoints must return 401/403 without a valid token."""
    import httpx

    transport = httpx.ASGITransport(app=nutrition_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/nutrition/today?chat_id=default")

    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 7. Nutrition router is mounted on the app
# ---------------------------------------------------------------------------

class TestNutritionRouterMounted:
    def test_nutrition_router_registered_on_app(self):
        """The nutrition router must be included in the FastAPI app routes."""
        with patch.dict(sys.modules, _stub_missing_modules()):
            with patch("app.memory.store._get_conn", side_effect=RuntimeError("no db")):
                from app.main import app

        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/nutrition/today" in routes, (
            f"Expected /api/nutrition/today in routes, got: {routes}"
        )
        assert "/api/nutrition/log-water" in routes
        assert "/api/nutrition/log-text" in routes

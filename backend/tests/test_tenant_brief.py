"""Per-tenant morning brief (product/brief.py): opt-in, once a day, own scope."""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture()
def settings_store():
    """In-memory app_settings keyed by (tenant scope, key)."""
    from app.context import current_tenant_id
    data: dict = {}

    def get(key):
        return data.get((current_tenant_id.get(), key))

    def put(key, value):
        data[(current_tenant_id.get(), key)] = value

    with patch("app.memory.store.get_app_setting", side_effect=get), \
         patch("app.memory.store.set_app_setting", side_effect=put):
        yield data


AT_8 = datetime(2026, 10, 24, 8, 0)
AT_6 = datetime(2026, 10, 24, 6, 0)


def test_brief_is_off_by_default(settings_store):
    from product import brief
    with patch("app.morning_brief.generate", AsyncMock(return_value="x")) as gen:
        assert _run(brief.run_for_scope("t1", now=AT_8)) is False
    gen.assert_not_called()


def test_brief_sent_once_after_its_time_in_the_tenant_scope(settings_store):
    from product import brief
    from app.context import current_tenant_id
    settings_store[("t1", brief.SETTINGS_KEY)] = {"enabled": True, "time": "07:30"}
    seen_scopes = []

    async def fake_generate(chat_id):
        seen_scopes.append((current_tenant_id.get(), chat_id))
        return "🌅 בוקר טוב"

    with patch("app.morning_brief.generate", side_effect=fake_generate), \
         patch.object(brief, "_deliver", AsyncMock()) as deliver, \
         patch.object(brief, "_today", return_value="2026-10-24"):
        assert _run(brief.run_for_scope("t1", now=AT_6)) is False   # too early
        assert _run(brief.run_for_scope("t1", now=AT_8)) is True
        assert _run(brief.run_for_scope("t1", now=AT_8)) is False   # already sent today
        assert _run(brief.run_for_scope("t2", now=AT_8)) is False   # t2 never opted in

    assert seen_scopes == [("t1", "web_t1")]
    deliver.assert_awaited_once_with("web_t1", "🌅 בוקר טוב")
    assert settings_store[("t1", brief.CACHE_KEY)]["text"] == "🌅 בוקר טוב"


def test_today_brief_is_cached_for_the_day(settings_store):
    from product import brief
    with patch("app.morning_brief.generate", AsyncMock(return_value="brief")) as gen, \
         patch.object(brief, "_today", return_value="2026-10-24"):
        assert _run(brief.today_brief("web_t1")) == "brief"
        assert _run(brief.today_brief("web_t1")) == "brief"
    gen.assert_awaited_once()


def test_save_settings_rejects_bad_time(settings_store):
    from product import brief
    with pytest.raises(ValueError):
        brief.save_settings(True, "25:99")
    assert brief.save_settings(True, "06:45") == {"enabled": True, "time": "06:45"}

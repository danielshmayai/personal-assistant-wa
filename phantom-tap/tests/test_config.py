from __future__ import annotations

from datetime import date

import pytest

from phantom_tap.config import Config, ConfigError, Watch


def base(**over):
    data = {
        "account": {"username": "0501234567", "club_id": "modiin"},
        "watch": [{"class_name": "BODY POWER", "weekday": "wednesday", "start_time": "19:10"}],
    }
    data.update(over)
    return data


def test_hebrew_weekday_names_work():
    cfg = Config.from_dict(base(watch=[
        {"class_name": "HIIT", "weekday": "חמישי", "start_time": "18:30"}
    ]))
    assert cfg.watches[0].weekday == 3  # Thursday


def test_next_date_rolls_to_next_week_not_backwards():
    watch = Watch(class_name="X", weekday=2, start_time=__import__("datetime").time(19, 10))
    monday, wednesday, thursday = date(2026, 9, 14), date(2026, 9, 16), date(2026, 9, 17)
    assert watch.next_date(after=monday) == wednesday
    assert watch.next_date(after=wednesday) == wednesday, "today still counts"
    assert watch.next_date(after=thursday) == date(2026, 9, 23)


def test_waha_without_a_chat_id_is_rejected():
    with pytest.raises(ConfigError, match="chat_id is empty"):
        Config.from_dict(base(notify={"backend": "waha"}))


def test_duplicate_preferred_spots_are_rejected():
    with pytest.raises(ConfigError, match="duplicates"):
        Config.from_dict(base(watch=[
            {"class_name": "X", "weekday": "sunday", "start_time": "08:00",
             "preferred_spots": [12, 12]}
        ]))


def test_unknown_seat_fallback_is_rejected():
    with pytest.raises(ConfigError, match="seat_fallback"):
        Config.from_dict(base(watch=[
            {"class_name": "X", "weekday": "sunday", "start_time": "08:00",
             "seat_fallback": "whatever"}
        ]))


def test_disabled_watches_are_filtered():
    cfg = Config.from_dict(base(watch=[
        {"class_name": "A", "weekday": "sunday", "start_time": "08:00"},
        {"class_name": "B", "weekday": "monday", "start_time": "09:00", "enabled": False},
    ]))
    assert [w.class_name for w in cfg.enabled_watches()] == ["A"]

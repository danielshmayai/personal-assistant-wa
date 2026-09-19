"""User configuration: which classes to chase, and how to talk to the world.

Note what is *not* here: the moment registration opens. That is read off the
server for each class, because the app publishes it per class and a human typing
it is the single most likely way to miss a booking.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from phantom_tap.holmesplace.models import ISRAEL

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    # The club runs on a Sunday-start week and the app labels days in Hebrew.
    "ראשון": 6, "שני": 0, "שלישי": 1, "רביעי": 2, "חמישי": 3, "שישי": 4, "שבת": 5,
}
SEAT_FALLBACKS = ("nearest", "any", "none")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Watch:
    class_name: str
    weekday: int
    start_time: time
    preferred_spots: tuple[int, ...] = ()
    seat_fallback: str = "nearest"
    enabled: bool = True

    @property
    def start_hhmm(self) -> str:
        return self.start_time.strftime("%H:%M")

    def next_date(self, *, after: date | None = None) -> date:
        """The next calendar date this class runs on, today included."""
        today = after or datetime.now(tz=ISRAEL).date()
        return today + timedelta(days=(self.weekday - today.weekday()) % 7)

    def label(self) -> str:
        return f"{self.class_name} {self.start_hhmm}"


@dataclass(frozen=True)
class NotifyConfig:
    backend: str = "log"  # waha | log | none
    waha_url: str = "http://localhost:3000"
    waha_session: str = "default"
    chat_id: str = ""


@dataclass(frozen=True)
class Config:
    username: str
    club_id: str
    watches: tuple[Watch, ...]
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    endpoints_path: Path = Path("config/endpoints.json")
    db_path: Path = Path("data/phantom.sqlite3")
    key_path: Path = Path("config/phantom.key")
    secrets_path: Path = Path("config/secrets.enc")
    # How far ahead to log in, warm the connection and resolve the class id.
    # Five minutes is comfortably longer than any of those take and comfortably
    # shorter than a typical access-token lifetime.
    lead_in_seconds: float = 300.0

    @classmethod
    def load(cls, path: str | Path) -> Config:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"{p} does not exist. Copy config/booking.example.toml to {p}.")
        return cls.from_dict(tomllib.loads(p.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        account = data.get("account") or {}
        for key in ("username", "club_id"):
            if not account.get(key):
                raise ConfigError(f"[account] is missing {key}")

        runtime = data.get("runtime") or {}
        notify = NotifyConfig(**(data.get("notify") or {}))
        if notify.backend not in ("waha", "log", "none"):
            raise ConfigError(f"notify.backend must be waha, log or none (got {notify.backend!r})")
        if notify.backend == "waha" and not notify.chat_id:
            raise ConfigError("notify.backend is 'waha' but notify.chat_id is empty")

        watches = tuple(_watch(w, i) for i, w in enumerate(data.get("watch") or []))
        if not watches:
            raise ConfigError("no [[watch]] entries - there is nothing to book")

        return cls(
            username=str(account["username"]),
            club_id=str(account["club_id"]),
            watches=watches,
            notify=notify,
            endpoints_path=Path(runtime.get("endpoints", "config/endpoints.json")),
            db_path=Path(runtime.get("db", "data/phantom.sqlite3")),
            key_path=Path(runtime.get("key", "config/phantom.key")),
            secrets_path=Path(runtime.get("secrets", "config/secrets.enc")),
            lead_in_seconds=float(runtime.get("lead_in_seconds", 300.0)),
        )

    def enabled_watches(self) -> tuple[Watch, ...]:
        return tuple(w for w in self.watches if w.enabled)


def _watch(raw: dict[str, Any], index: int) -> Watch:
    where = f"[[watch]] #{index + 1}"
    name = str(raw.get("class_name") or "").strip()
    if not name:
        raise ConfigError(f"{where} is missing class_name")

    key = str(raw.get("weekday") or "").strip().casefold()
    if key not in WEEKDAYS:
        raise ConfigError(f"{where}: weekday {raw.get('weekday')!r} is not a day name")

    try:
        hh, mm = str(raw["start_time"]).split(":")
        start = time(int(hh), int(mm))
    except (KeyError, ValueError) as exc:
        raise ConfigError(f"{where}: start_time must look like '19:10'") from exc

    fallback = str(raw.get("seat_fallback", "nearest"))
    if fallback not in SEAT_FALLBACKS:
        raise ConfigError(f"{where}: seat_fallback must be one of {', '.join(SEAT_FALLBACKS)}")

    spots = tuple(int(s) for s in raw.get("preferred_spots", ()))
    if len(set(spots)) != len(spots):
        raise ConfigError(f"{where}: preferred_spots has duplicates: {spots}")

    return Watch(
        class_name=name,
        weekday=WEEKDAYS[key],
        start_time=start,
        preferred_spots=spots,
        seat_fallback=fallback,
        enabled=bool(raw.get("enabled", True)),
    )

"""Types for the Holmes Place protocol, plus the config that describes it.

The protocol is private and undocumented, so it cannot be hard-coded honestly:
it is *discovered* by `pt capture` + `pt analyze` and written to
`config/endpoints.json`. This module is the boundary between that discovered
description and typed Python the rest of the code can rely on.

Everything the app showed us on screen has a home here: a class carries the
moment its registration opens (the app renders it as "פתיחת הרשמה: 16/09 14:10"),
its registered/capacity pair (the "43 / 45" bar) and whether it needs a seat
chosen off the studio floor map.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from phantom_tap import pathspec

ISRAEL = ZoneInfo("Asia/Jerusalem")

# Observed on every class the app showed: registration opens exactly 5h before the
# class starts. We never *use* this to compute anything - the server is the source
# of truth - but a mismatch means the rule changed or we parsed the wrong field,
# and that is worth shouting about before it costs a booking.
EXPECTED_LEAD = timedelta(hours=5)


class ProtocolError(RuntimeError):
    """The server did not answer in the shape `endpoints.json` promised."""


# --------------------------------------------------------------------------- #
# Domain
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClassSlot:
    id: str
    name: str
    start: datetime
    end: datetime | None = None
    opens_at: datetime | None = None
    registered: int | None = None
    capacity: int | None = None
    instructor: str | None = None
    studio: str | None = None
    has_seats: bool = False
    booked: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_full(self) -> bool:
        if self.registered is None or self.capacity is None:
            return False
        return self.registered >= self.capacity

    @property
    def lead_matches_expectation(self) -> bool:
        """Does this class follow the observed start-minus-5h rule?"""
        if self.opens_at is None:
            return False
        return abs((self.start - self.opens_at) - EXPECTED_LEAD) < timedelta(minutes=1)

    def describe(self) -> str:
        when = self.start.astimezone(ISRAEL).strftime("%d/%m %H:%M")
        bits = [f"{self.name} {when}"]
        if self.studio:
            bits.append(self.studio)
        if self.registered is not None and self.capacity is not None:
            bits.append(f"{self.registered}/{self.capacity}")
        return " · ".join(bits)


@dataclass(frozen=True)
class Seat:
    number: int
    available: bool


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str
    club_id: str

    def __repr__(self) -> str:  # keep the password out of tracebacks and logs
        return f"Credentials(username={self.username!r}, club_id={self.club_id!r}, password=***)"


# --------------------------------------------------------------------------- #
# Discovered protocol description
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    query: dict[str, str] = field(default_factory=dict)
    json_body: dict[str, Any] | None = None
    items_path: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    success_status: tuple[int, ...] = (200, 201, 204)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Endpoint:
        return cls(
            method=data.get("method", "GET").upper(),
            path=data["path"],
            query=data.get("query", {}),
            json_body=data.get("json"),
            items_path=data.get("items_path"),
            fields=data.get("fields", {}),
            success_status=tuple(data.get("success_status", (200, 201, 204))),
        )


@dataclass(frozen=True)
class Signals:
    """Server phrases mapped to the only four outcomes the racer cares about.

    Filled in from what the real API says during capture. Matching is done on the
    lowercased response body, so Hebrew and English phrasings sit side by side.
    """

    not_open: tuple[str, ...] = ()
    full: tuple[str, ...] = ()
    already: tuple[str, ...] = ()
    seat_taken: tuple[str, ...] = ()

    def classify(self, body: str) -> str | None:
        low = body.lower()
        for label, needles in (
            ("not_open", self.not_open),
            ("full", self.full),
            ("already", self.already),
            ("seat_taken", self.seat_taken),
        ):
            if any(n.lower() in low for n in needles):
                return label
        return None


@dataclass(frozen=True)
class Endpoints:
    base_url: str
    login: Endpoint
    schedule: Endpoint
    register: Endpoint
    headers: dict[str, str] = field(default_factory=dict)
    auth_header: str = "Authorization"
    auth_format: str = "Bearer {token}"
    token_path: str = "token"
    seat_map: Endpoint | None = None
    confirm_seat: Endpoint | None = None
    my_bookings: Endpoint | None = None
    signals: Signals = field(default_factory=Signals)

    @classmethod
    def load(cls, path: str | Path) -> Endpoints:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"{p} does not exist. The Holmes Place protocol is discovered, not "
                f"shipped: run `pt capture` then `pt analyze` on the machine with the "
                f"Android device attached."
            )
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Endpoints:
        auth = data.get("auth", {})
        opt = lambda k: Endpoint.from_dict(data[k]) if data.get(k) else None  # noqa: E731
        return cls(
            base_url=data["base_url"].rstrip("/"),
            headers=data.get("headers", {}),
            auth_header=auth.get("header", "Authorization"),
            auth_format=auth.get("format", "Bearer {token}"),
            token_path=data["login"]["token_path"],
            login=Endpoint.from_dict(data["login"]),
            schedule=Endpoint.from_dict(data["schedule"]),
            register=Endpoint.from_dict(data["register"]),
            seat_map=opt("seat_map"),
            confirm_seat=opt("confirm_seat"),
            my_bookings=opt("my_bookings"),
            signals=Signals(**{k: tuple(v) for k, v in data.get("signals", {}).items()}),
        )


# --------------------------------------------------------------------------- #
# Template substitution and parsing
# --------------------------------------------------------------------------- #

_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def render(value: Any, ctx: dict[str, Any]) -> Any:
    """Substitute `{name}` placeholders through strings, dicts and lists.

    Deliberately not `str.format`: a captured body can legitimately contain braces,
    and an unknown placeholder must be a loud error rather than a KeyError from
    deep inside the stdlib at T0.
    """
    if isinstance(value, str):
        # A string that is exactly one placeholder keeps the substituted type,
        # so {class_id} can stay an int if the API wants an int.
        whole = _PLACEHOLDER.fullmatch(value)
        if whole:
            return _lookup(whole.group(1), ctx, value)
        return _PLACEHOLDER.sub(lambda m: str(_lookup(m.group(1), ctx, value)), value)
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def _lookup(name: str, ctx: dict[str, Any], origin: str) -> Any:
    if name not in ctx:
        known = ", ".join(sorted(ctx)) or "<none>"
        raise ProtocolError(f"{origin!r} needs {{{name}}}, which is not set. Known: {known}")
    return ctx[name]


_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%y %H:%M",
    "%Y-%m-%d",
)


def parse_dt(value: Any) -> datetime | None:
    """Best-effort timestamp parse, always returning an aware datetime.

    Undocumented APIs are inconsistent about this - the same backend happily emits
    ISO-8601, a unix epoch and the dd/MM/yyyy the Hebrew UI renders. A naive result
    is assumed to be Israel local, which is what the club runs on.
    """
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        # Milliseconds if it is far too large to be seconds.
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=ISRAEL)
    if not isinstance(value, str):
        return None

    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=ISRAEL)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=ISRAEL)
        except ValueError:
            continue
    return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def slot_from(item: dict[str, Any], fields: dict[str, str]) -> ClassSlot:
    """Turn one raw schedule entry into a ClassSlot using the discovered field map."""

    def get(name: str) -> Any:
        path = fields.get(name)
        return pathspec.resolve(item, path) if path else None

    slot_id, start = get("id"), parse_dt(get("start"))
    if slot_id is None or start is None:
        raise ProtocolError(
            f"schedule entry lacks id/start under the configured field map "
            f"(id={fields.get('id')!r}, start={fields.get('start')!r}); "
            f"keys present: {', '.join(sorted(item)[:12])}"
        )
    return ClassSlot(
        id=str(slot_id),
        name=str(get("name") or "").strip(),
        start=start,
        end=parse_dt(get("end")),
        opens_at=parse_dt(get("opens_at")),
        registered=_as_int(get("registered")),
        capacity=_as_int(get("capacity")),
        instructor=(str(get("instructor")).strip() or None) if get("instructor") else None,
        studio=(str(get("studio")).strip() or None) if get("studio") else None,
        has_seats=bool(get("has_seats")),
        booked=bool(get("booked")),
        raw=item,
    )

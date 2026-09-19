"""A stand-in for the Holmes Place backend.

It exists so the race can be tested against the behaviours that actually decide a
booking, none of which a mocked-out client would exercise:

  * registration refused before T0, accepted from T0 onward
  * a class that fills up while we are retrying
  * a seat that another member claims between our two phases
  * a backend that returns a 500 once and then works

The protocol shape here is invented - the real one is discovered by `pt analyze` -
but it is invented in the awkward style these backends really have (a `d` envelope,
`dd/MM/yyyy` timestamps, `isAvailable` booleans) so the field mapping and the date
parsing are genuinely under test rather than being handed a tidy schema.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta

import httpx

from phantom_tap.holmesplace.models import ISRAEL

CLASS_ID = "77104"
CAPACITY = 45
TOTAL_SEATS = 48


class FakeHolmesPlace:
    def __init__(
        self,
        *,
        t0: float,
        registered: int = 0,
        taken_seats: set[int] | None = None,
        fills_at: float | None = None,
        fail_first_n: int = 0,
        class_name: str = "BODY POWER",
    ) -> None:
        self.t0 = t0
        self.registered_count = registered
        self.taken_seats = set(taken_seats or ())
        self.fills_at = fills_at
        self.fail_first_n = fail_first_n
        self.class_name = class_name

        self.register_calls = 0
        self.seat_calls: list[int] = []
        self.booked = False
        self.my_seat: int | None = None
        self.token = "tok-" + "a" * 40

    # -------------------------------------------------------------- helpers --

    @property
    def start(self) -> datetime:
        # The observed rule: registration opens five hours before the class.
        return datetime.fromtimestamp(self.t0, tz=ISRAEL) + timedelta(hours=5)

    def _is_full(self) -> bool:
        if self.fills_at is not None and time.time() >= self.fills_at:
            return True
        return self.registered_count >= CAPACITY

    def _class_json(self) -> dict:
        return {
            "lessonId": CLASS_ID,
            "lessonName": self.class_name,
            "startDateTime": self.start.strftime("%d/%m/%Y %H:%M"),
            "endDateTime": (self.start + timedelta(minutes=50)).strftime("%d/%m/%Y %H:%M"),
            # Deliberately opaque name: only the five-hour arithmetic finds this.
            "rgDt": datetime.fromtimestamp(self.t0, tz=ISRAEL).strftime("%d/%m/%Y %H:%M"),
            "participantsCount": self.registered_count,
            "maxCapacity": CAPACITY,
            "instructorName": "סיון אריאלי",
            "roomName": "סטודיו 1",
            "hasSpots": True,
            "isRegistered": self.booked,
        }

    # ------------------------------------------------------------- handlers --

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}

        if path.endswith("/auth/login"):
            if body.get("password") != "hunter2":
                return _json(401, {"message": "Bad credentials"})
            return _json(200, {"d": {"authToken": self.token}})

        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return _json(401, {"message": "Unauthorized"})

        if path.endswith("/schedule"):
            return _json(200, {"d": {"items": [self._class_json()]}})

        if path.endswith("/me/bookings"):
            items = (
                [{"lessonId": CLASS_ID, "spotNumber": self.my_seat}] if self.booked else []
            )
            return _json(200, {"d": {"items": items}})

        if re.fullmatch(rf"/api/v2/classes/{CLASS_ID}/spots", path):
            spots = [
                {"spotNumber": n, "isAvailable": n not in self.taken_seats}
                for n in range(1, TOTAL_SEATS + 1)
            ]
            return _json(200, {"d": {"spots": spots}})

        if m := re.fullmatch(rf"/api/v2/classes/{CLASS_ID}/spots/(\d+)", path):
            return self._claim_seat(int(m.group(1)))

        if path == f"/api/v2/classes/{CLASS_ID}/register":
            return self._register()

        return _json(404, {"message": f"no route for {path}"})

    def _register(self) -> httpx.Response:
        self.register_calls += 1
        if self.register_calls <= self.fail_first_n:
            return _json(503, {"message": "Service Unavailable"})
        if time.time() < self.t0:
            return _json(400, {"message": "Registration has not opened yet"})
        if self.booked:
            return _json(409, {"message": "You are already registered for this class"})
        if self._is_full():
            return _json(400, {"message": "The class is full"})
        self.booked = True
        self.registered_count += 1
        return _json(200, {"d": {"lessonId": CLASS_ID, "status": "registered"}})

    def _claim_seat(self, seat: int) -> httpx.Response:
        self.seat_calls.append(seat)
        if not self.booked:
            return _json(400, {"message": "Register for the class first"})
        if seat in self.taken_seats:
            return _json(409, {"message": "This seat is taken"})
        self.taken_seats.add(seat)
        self.my_seat = seat
        return _json(200, {"d": {"spotNumber": seat}})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


def _json(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(status, json=payload)


#: The endpoints description `pt analyze` would produce for this backend.
ENDPOINTS = {
    "base_url": "https://api.holmesplace.co.il",
    "headers": {"x-app-version": "5.2.1", "accept-language": "he-IL"},
    "auth": {"header": "Authorization", "format": "Bearer {token}"},
    "login": {
        "method": "POST",
        "path": "/api/v2/auth/login",
        "json": {"userName": "{username}", "password": "{password}", "clubId": "{club_id}"},
        "token_path": "d.authToken",
        "success_status": [200],
    },
    "schedule": {
        "method": "GET",
        "path": "/api/v2/schedule",
        "query": {"clubId": "{club_id}", "date": "{date}"},
        "items_path": "d.items",
        "fields": {
            "id": "lessonId",
            "name": "lessonName",
            "start": "startDateTime",
            "end": "endDateTime",
            "opens_at": "rgDt",
            "registered": "participantsCount",
            "capacity": "maxCapacity",
            "instructor": "instructorName",
            "studio": "roomName",
            "has_seats": "hasSpots",
            "booked": "isRegistered",
        },
    },
    "register": {
        "method": "POST",
        "path": "/api/v2/classes/{class_id}/register",
        "json": {},
        "success_status": [200],
    },
    "seat_map": {
        "method": "GET",
        "path": "/api/v2/classes/{class_id}/spots",
        "items_path": "d.spots",
        "fields": {"number": "spotNumber", "available": "isAvailable"},
    },
    "confirm_seat": {
        "method": "POST",
        "path": "/api/v2/classes/{class_id}/spots/{seat}",
        "json": {},
        "success_status": [200],
    },
    "my_bookings": {
        "method": "GET",
        "path": "/api/v2/me/bookings",
        "items_path": "d.items",
        "fields": {"class_id": "lessonId", "seat": "spotNumber"},
    },
    "signals": {
        "not_open": ["Registration has not opened yet"],
        "full": ["The class is full"],
        "already": ["already registered"],
        "seat_taken": ["This seat is taken"],
    },
}

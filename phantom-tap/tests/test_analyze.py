"""Capture -> analyze -> a client that actually books.

The strongest claim this project makes is that the protocol can be *recovered*
rather than hand-written. These tests make that claim falsifiable: they build a
capture the way mitmproxy would (secrets already replaced by markers), run the
inference over it, and then hand the inferred description to the real client and
race a real booking with it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from phantom_tap.capture.analyze import analyze, load
from phantom_tap.capture.mitm_addon import redact
from phantom_tap.holmesplace.api import HolmesPlaceClient
from phantom_tap.holmesplace.models import Credentials, Endpoints
from phantom_tap.race import RacePlan, race
from tests.fake_holmesplace import CLASS_ID, FakeHolmesPlace

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.Hs9K3mQpL7vRtYuIoPaSdFgHjKlZxCvBnM"


def capture_lines(backend: FakeHolmesPlace) -> list[dict]:
    """The exchanges a human would generate driving the app once, through mitmproxy."""
    auth = {"authorization": redact(f"Bearer {TOKEN}", "authorization")}
    app = {"x-app-version": "5.2.1", "accept-language": "he-IL"}
    # A real day's schedule is a list, and the detector has to pick the class
    # list out of a response that also carries banners and other noise.
    others = []
    for offset, name in ((-2, "פלדנקרייז"), (1, "HIIT")):
        other = dict(backend._class_json())
        other["lessonId"] = str(int(backend._class_json()["lessonId"]) + offset)
        other["lessonName"] = name
        others.append(other)
    schedule_body = {
        "d": {
            "items": [others[0], backend._class_json(), others[1]],
            "banners": [{"imageUrl": "https://cdn/x.png", "order": 1}],
        }
    }
    spots = {"d": {"spots": [{"spotNumber": n, "isAvailable": n != 4} for n in range(1, 49)]}}

    def line(method, path, status, req=None, resp=None, query=None, headers=None):
        return {
            "ts": time.time(),
            "method": method,
            "scheme": "https",
            "host": "api.holmesplace.co.il",
            "path": path,
            "query": query or {},
            "req_headers": {**app, **(headers or {})},
            "req_body": redact(req) if req else None,
            "status": status,
            "resp_body": redact(resp) if resp else None,
        }

    return [
        line("POST", "/api/v2/auth/login", 200,
             req={"userName": "0501234567", "password": "hunter2", "clubId": "modiin"},
             resp={"d": {"authToken": TOKEN}}),
        line("GET", "/api/v2/schedule", 200, resp=schedule_body,
             query={"clubId": "modiin", "date": "2026-09-16"}, headers=auth),
        # An early tap, which is how the refusal phrase gets learned.
        line("POST", f"/api/v2/classes/{CLASS_ID}/register", 400, req={},
             resp={"message": "Registration has not opened yet"}, headers=auth),
        line("POST", f"/api/v2/classes/{CLASS_ID}/register", 200, req={},
             resp={"d": {"lessonId": CLASS_ID, "status": "registered"}}, headers=auth),
        line("GET", f"/api/v2/classes/{CLASS_ID}/spots", 200, resp=spots, headers=auth),
        line("POST", f"/api/v2/classes/{CLASS_ID}/spots/12", 200, req={},
             resp={"d": {"spotNumber": 12}}, headers=auth),
        line("GET", "/api/v2/me/bookings", 200,
             resp={"d": {"items": [{"lessonId": CLASS_ID, "spotNumber": 12}]}}, headers=auth),
    ]


def write_capture(tmp_path: Path, backend: FakeHolmesPlace) -> Path:
    path = tmp_path / "capture.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in capture_lines(backend)),
        encoding="utf-8",
    )
    return path


def detect(tmp_path: Path, backend: FakeHolmesPlace):
    return analyze(load(write_capture(tmp_path, backend)))


def test_no_secret_survives_the_capture(tmp_path, make_backend):
    """Whatever else analyze gets wrong, the capture must be safe to share."""
    text = write_capture(tmp_path, make_backend()).read_text(encoding="utf-8")
    assert TOKEN not in text
    assert "hunter2" not in text
    assert "«SECRET:" in text


def test_finds_login_and_proves_the_token_field(tmp_path, make_backend):
    det = detect(tmp_path, make_backend())
    login = det.endpoints["login"]

    assert login["path"] == "/api/v2/auth/login"
    # Found by correlating the marker with the Authorization header, not by name.
    assert login["token_path"] == "d.authToken"
    assert login["json"] == {
        "userName": "{username}", "password": "{password}", "clubId": "{club_id}"
    }
    assert det.endpoints["auth"]["format"] == "Bearer {token}"


def test_finds_the_registration_open_field_by_arithmetic(tmp_path, make_backend):
    """`rgDt` is named nothing useful. Five hours before the start is what finds it."""
    det = detect(tmp_path, make_backend())
    fields = det.endpoints["schedule"]["fields"]

    assert fields["opens_at"] == "rgDt"
    assert fields["start"] == "startDateTime"
    assert any("5h before" in n for n in det.notes)


def test_maps_the_rest_of_the_schedule_fields(tmp_path, make_backend):
    fields = detect(tmp_path, make_backend()).endpoints["schedule"]["fields"]

    assert fields["id"] == "lessonId"
    assert fields["capacity"] == "maxCapacity"
    assert fields["registered"] == "participantsCount"
    assert fields["has_seats"] == "hasSpots"  # a bool, not mistaken for a count
    assert fields["capacity"] != "hasSpots"


def test_separates_register_from_seat_confirm(tmp_path, make_backend):
    """The register path contains a standalone '2' (from /v2/) - not a seat claim."""
    det = detect(tmp_path, make_backend())

    assert det.endpoints["register"]["path"] == "/api/v2/classes/{class_id}/register"
    assert det.endpoints["confirm_seat"]["path"] == "/api/v2/classes/{class_id}/spots/{seat}"
    assert det.endpoints["seat_map"]["path"] == "/api/v2/classes/{class_id}/spots"


def test_learns_the_refusal_phrase(tmp_path, make_backend):
    signals = detect(tmp_path, make_backend()).endpoints["signals"]
    assert any("not" in s.lower() and "open" in s.lower() for s in signals["not_open"])


def test_nothing_essential_is_missing(tmp_path, make_backend):
    assert detect(tmp_path, make_backend()).missing == []


async def test_the_inferred_protocol_actually_books(tmp_path, make_backend, clock):
    """The whole point, end to end: a capture in, a confirmed booking out."""
    backend = make_backend(opens_in=0.2)
    inferred = Endpoints.from_dict(detect(tmp_path, backend).endpoints)

    client = HolmesPlaceClient(
        inferred,
        Credentials(username="0501234567", password="hunter2", club_id="modiin"),
        transport=backend.transport(),
    )
    await client.login()
    # The fake's class starts five hours after its T0, which is relative to now.
    slot = await client.find_class(
        backend.start.date(), "BODY POWER", backend.start.strftime("%H:%M")
    )
    assert slot is not None and slot.opens_at is not None
    assert slot.lead_matches_expectation

    # rgDt carries only minutes, so the plan must widen its burst to cover the
    # whole minute rather than trusting a second that was never published.
    plan = RacePlan.from_slot(slot, preferred_spots=(12, 13))
    assert plan.t0_precision == 60.0
    result = await race(client, plan, clock)
    await client.aclose()

    assert result.won, result.reason
    assert result.seat == 12

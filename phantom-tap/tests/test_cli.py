"""The CLI, end to end where it matters: a capture file in, a booking out.

These drive `pt` through its argv the way a shell does, against the fake backend,
so the wiring between commands - analyze writing endpoints.json, book reading it -
is under test rather than assumed.
"""

from __future__ import annotations

import json

import pytest

from phantom_tap import cli
from tests.fake_holmesplace import ENDPOINTS, FakeHolmesPlace
from tests.test_analyze import capture_lines

# The days the app labels in Hebrew, indexed the way datetime.weekday() does.
_HEB_DAY = {0: "שני", 1: "שלישי", 2: "רביעי", 3: "חמישי", 4: "שישי", 5: "שבת", 6: "ראשון"}

_BASE_CONFIG = """
[account]
username = "0501234567"
club_id  = "modiin"
[runtime]
endpoints = "config/endpoints.json"
db        = "data/phantom.sqlite3"
key       = "config/phantom.key"
secrets   = "config/secrets.enc"
[notify]
backend = "log"
[[watch]]
class_name = "BODY POWER"
weekday    = "wednesday"
start_time = "19:10"
preferred_spots = [12, 13]
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A working directory laid out like a real install, minus the protocol."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "booking.toml").write_text(_BASE_CONFIG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def aligned_backend(project, *, opens_in: float) -> FakeHolmesPlace:
    """A fake whose window opens `opens_in` from now, with a config written to match.

    `book` resolves a class by (weekday, HH:MM), so instead of bending the fake to
    a fixed config time we do the reverse: pick a T0 a moment away, let the fake's
    class fall where it will (T0 + 5h), and write watch #1 to point exactly there.
    This keeps the test fast and still exercises the real resolve-by-day path.
    """
    import time as _t

    backend = FakeHolmesPlace(t0=_t.time() + opens_in)
    start = backend.start
    lines = [
        "[account]",
        'username = "0501234567"',
        'club_id  = "modiin"',
        "[runtime]",
        'endpoints = "config/endpoints.json"',
        'db        = "data/phantom.sqlite3"',
        'key       = "config/phantom.key"',
        'secrets   = "config/secrets.enc"',
        "lead_in_seconds = 0",
        "[notify]",
        'backend = "log"',
        "[[watch]]",
        'class_name = "BODY POWER"',
        'weekday    = "' + _HEB_DAY[start.weekday()] + '"',
        'start_time = "' + start.strftime("%H:%M") + '"',
        "preferred_spots = [12, 13]",
    ]
    (project / "config" / "booking.toml").write_text("\n".join(lines), encoding="utf-8")
    return backend


def _write_capture(project, backend):
    cap = project / "cap.jsonl"
    cap.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in capture_lines(backend)),
        encoding="utf-8",
    )
    return cap


def test_analyze_writes_endpoints_from_a_capture(project):
    cap = _write_capture(project, FakeHolmesPlace(t0=0))

    assert cli.main(["analyze", str(cap)]) == 0

    written = json.loads((project / "config" / "endpoints.json").read_text(encoding="utf-8"))
    assert written["login"]["token_path"] == "d.authToken"
    assert written["schedule"]["fields"]["opens_at"] == "rgDt"


def test_analyze_refuses_to_clobber_without_force(project):
    cap = _write_capture(project, FakeHolmesPlace(t0=0))
    assert cli.main(["analyze", str(cap)]) == 0
    assert cli.main(["analyze", str(cap)]) == 1  # exists now
    assert cli.main(["analyze", str(cap), "--force"]) == 0


def test_book_rehearsal_sends_no_registration(project, monkeypatch, capsys):
    """The default `book` is a rehearsal: it may read, but must never register."""
    backend = aligned_backend(project, opens_in=0.15)
    _install_protocol(project, backend)
    _patch_transport(monkeypatch, backend)

    rc = cli.main(["book", "--watch", "1", "--date", backend.start.date().isoformat()])
    assert rc == 0
    assert "REHEARSAL" in capsys.readouterr().out
    assert backend.register_calls == 0, "a rehearsal must not send a registration"
    assert not backend.booked


def test_history_reads_back_a_recorded_race(project, monkeypatch, capsys):
    backend = aligned_backend(project, opens_in=0.15)
    _install_protocol(project, backend)
    _patch_transport(monkeypatch, backend)

    rc = cli.main(["book", "--watch", "1", "--live", "--date", backend.start.date().isoformat()])
    assert rc == 0
    assert backend.booked
    assert cli.main(["history"]) == 0
    assert "booked" in capsys.readouterr().out


def _install_protocol(project, backend):
    (project / "config" / "endpoints.json").write_text(
        json.dumps(ENDPOINTS, ensure_ascii=False), encoding="utf-8"
    )
    from phantom_tap.secrets import SecretStore

    store = SecretStore(project / "config" / "phantom.key", project / "config" / "secrets.enc")
    store.set("hp_password", "hunter2")


def _patch_transport(monkeypatch, backend):
    """Make every client the orchestrator builds talk to the fake."""
    import phantom_tap.orchestrator as orch

    real = orch.HolmesPlaceClient

    def factory(endpoints, credentials, **kw):
        return real(endpoints, credentials, transport=backend.transport())

    monkeypatch.setattr(orch, "HolmesPlaceClient", factory)
    # No NTP in tests: the local clock is the reference the fake also uses, and a
    # real sync would reach for the network (and time out) mid-test.
    monkeypatch.setattr(orch, "_sync_clock", lambda: None)

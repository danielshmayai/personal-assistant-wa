"""The clock, which decides whether a correct racer arrives on time."""

from __future__ import annotations

import time

import pytest

from phantom_tap import clock as clockmod
from phantom_tap.clock import Clock, Sample, Sync, UnsyncedClock, sync


def fake_servers(monkeypatch, offsets: dict[str, float | None]) -> None:
    def fake_query(server: str, timeout: float = 2.0) -> Sample:
        offset = offsets[server]
        if offset is None:
            raise OSError("unreachable")
        return Sample(offset=offset, delay=0.01, server=server)

    monkeypatch.setattr(clockmod, "query_ntp", fake_query)


def test_median_ignores_one_wild_server(monkeypatch):
    """A single server behind a congested path must not drag the answer."""
    fake_servers(monkeypatch, {"a": 0.120, "b": 0.125, "c": 4.0})
    result = sync(("a", "b", "c"))
    assert result.offset == pytest.approx(0.125)


def test_unreachable_servers_are_skipped(monkeypatch):
    fake_servers(monkeypatch, {"a": None, "b": 0.2, "c": 0.2})
    result = sync(("a", "b", "c"))
    assert result.samples == 2
    assert result.offset == pytest.approx(0.2)


def test_total_failure_is_loud(monkeypatch):
    fake_servers(monkeypatch, {"a": None, "b": None})
    with pytest.raises(UnsyncedClock, match="no NTP server answered"):
        sync(("a", "b"))


def test_disagreement_is_not_trustworthy(monkeypatch):
    """Servers 200ms apart cannot support a race decided in 200ms."""
    fake_servers(monkeypatch, {"a": 0.0, "b": 0.2, "c": 0.1})
    assert not sync(("a", "b", "c")).trustworthy

    fake_servers(monkeypatch, {"a": 0.100, "b": 0.104, "c": 0.102})
    assert sync(("a", "b", "c")).trustworthy


def test_one_server_is_never_trustworthy(monkeypatch):
    """With a single sample there is nothing to cross-check against."""
    fake_servers(monkeypatch, {"a": 0.1, "b": None, "c": None})
    assert not sync(("a", "b", "c")).trustworthy


def test_now_applies_the_offset():
    clock = Clock(Sync(offset=1.5, dispersion=0.001, samples=3, synced_at_monotonic=time.monotonic()))
    assert clock.now() - time.time() == pytest.approx(1.5, abs=0.01)


def test_sleep_until_lands_within_a_millisecond():
    clock = Clock(None)
    target = clock.now() + 0.15

    started = time.monotonic()
    overshoot = clock.sleep_until(target)
    elapsed = time.monotonic() - started

    assert elapsed == pytest.approx(0.15, abs=0.01)
    assert 0 <= overshoot < 0.001, f"arrived {overshoot * 1000:.2f}ms late"


def test_a_past_deadline_returns_immediately():
    clock = Clock(None)
    started = time.monotonic()
    overshoot = clock.sleep_until(clock.now() - 5.0)
    assert time.monotonic() - started < 0.01
    assert overshoot == pytest.approx(5.0, abs=0.1)


def test_deadline_survives_the_wall_clock_being_stepped(monkeypatch):
    """ntpd stepping the clock mid-wait must not move our deadline.

    This is the whole reason the wait runs against the monotonic clock. If the
    deadline were recomputed from `time.time()`, a correction landing during the
    lead-in would move T0 and we would fire at the wrong moment.
    """
    clock = Clock(None)
    real_time = time.time
    target = real_time() + 0.1
    deadline = clock.monotonic_deadline(target)

    monkeypatch.setattr(time, "time", lambda: real_time() + 3600)  # ntpd jumps an hour
    assert clock.monotonic_deadline(target) < deadline - 3000  # a naive wait would break
    started = time.monotonic()
    while time.monotonic() < deadline:
        pass
    assert time.monotonic() - started == pytest.approx(0.1, abs=0.02)

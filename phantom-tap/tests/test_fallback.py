"""When the direct path breaks, the app on the phone books instead - if allowed.

The fallback taps a real phone, so it must run only when switched on, only for a
live booking, and only when the direct path is actually broken. And when the
phone is somewhere else - not on the same network, switched off - that has to be
reported rather than turning into a silent miss.
"""

from __future__ import annotations

import time
from typing import ClassVar

import httpx
import pytest

from phantom_tap.config import Config
from phantom_tap.holmesplace import ui as uimod
from phantom_tap.holmesplace.models import Endpoints, ProtocolError
from phantom_tap.notify import LogNotifier
from phantom_tap.orchestrator import book, expected_open
from phantom_tap.store import Store
from tests.fake_holmesplace import ENDPOINTS
from tests.test_cli import _install_protocol, _patch_transport, aligned_backend


class FakeAppOnPhone:
    """Stands in for HolmesPlaceUi; records how the orchestrator drove it."""

    calls: ClassVar[list[dict]] = []
    reachable = True

    def __init__(self, serial=None, shots=None):
        if not FakeAppOnPhone.reachable:
            raise uimod.UiUnavailable("cannot reach the device over adb: no route to host")
        self.serial = serial

    def book(self, day, class_name, start_hhmm, preferred, *, join_waitlist, opens_at):
        FakeAppOnPhone.calls.append(
            {"serial": self.serial, "class": class_name, "opens_at": opens_at, "preferred": preferred}
        )
        return uimod.UiResult(registered=True, seat=preferred[0], steps=[], screenshots=[])


@pytest.fixture
def phone(monkeypatch):
    FakeAppOnPhone.calls = []
    FakeAppOnPhone.reachable = True
    monkeypatch.setattr(uimod, "HolmesPlaceUi", FakeAppOnPhone)
    return FakeAppOnPhone


@pytest.fixture
def setup(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    monkeypatch.chdir(tmp_path)

    def _make(*, fallback: bool, password: str = "hunter2"):
        backend = aligned_backend(tmp_path, opens_in=0.15)
        toml = tmp_path / "config" / "booking.toml"
        if fallback:
            toml.write_text(
                toml.read_text(encoding="utf-8") + '\n[device]\nui_fallback = true\nserial = "192.168.1.50:5555"\n',
                encoding="utf-8",
            )
        _install_protocol(tmp_path, backend)
        if password != "hunter2":
            from phantom_tap.secrets import SecretStore

            SecretStore(tmp_path / "config" / "phantom.key", tmp_path / "config" / "secrets.enc").set(
                "hp_password", password
            )
        _patch_transport(monkeypatch, backend)
        config = Config.load(toml)
        return backend, config

    return _make


async def run(config: Config, backend, *, live: bool = True):
    store = Store(config.db_path)
    try:
        return await book(
            config,
            Endpoints.from_dict(ENDPOINTS),
            config.watches[0],
            store=store,
            notifier=LogNotifier(),
            live=live,
            day=backend.start.date(),
        ), store.history()
    finally:
        store.close()


async def test_broken_login_falls_back_to_the_phone_at_the_expected_time(setup, phone):
    """The server cannot be asked when T0 is, so the 5h rule supplies it."""
    backend, config = setup(fallback=True, password="wrong")  # the direct path is broken

    result, history = await run(config, backend)

    assert result.registered
    assert not result.verified, "nothing independent can confirm it while HTTP is broken"
    assert len(phone.calls) == 1
    call = phone.calls[0]
    assert call["serial"] == "192.168.1.50:5555"
    assert call["opens_at"] == pytest.approx(expected_open(config.watches[0], backend.start.date()))
    assert history[0]["class_id"].startswith("ui:")


async def test_rejected_at_t0_falls_back_with_the_servers_t0(setup, phone):
    backend, config = setup(fallback=True)
    backend._register = lambda: httpx.Response(403, json={"message": "Forbidden"})

    result, history = await run(config, backend)

    assert result.registered
    assert phone.calls[0]["opens_at"] == pytest.approx(backend.t0, abs=60)
    # Both the failed direct race and the phone booking are on record.
    assert len(history) == 2


async def test_fallback_is_off_unless_switched_on(setup, phone):
    backend, config = setup(fallback=False, password="wrong")

    with pytest.raises(ProtocolError):
        await run(config, backend)
    assert phone.calls == []


async def test_a_rehearsal_never_touches_the_phone(setup, phone):
    backend, config = setup(fallback=True, password="wrong")

    with pytest.raises(ProtocolError):
        await run(config, backend, live=False)
    assert phone.calls == []


async def test_a_working_direct_path_never_touches_the_phone(setup, phone):
    backend, config = setup(fallback=True)

    result, _ = await run(config, backend)

    assert result.won
    assert phone.calls == []


async def test_phone_out_of_reach_is_reported_not_silent(setup, phone, caplog):
    """The phone is on another network: say so, in the result and the alert."""
    phone.reachable = False
    backend, config = setup(fallback=True, password="wrong")

    started = time.time()
    result, history = await run(config, backend)

    assert not result.registered
    assert "could not start" in result.reason
    assert "no route to host" in result.reason
    assert history[0]["reason"] == result.reason
    assert time.time() - started < 5

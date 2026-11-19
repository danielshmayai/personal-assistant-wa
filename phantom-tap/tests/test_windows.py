"""Windows-only behaviour, exercised on any OS by faking the platform.

Running this on a home Windows PC is the expected setup, and each of these
covers something that failed there in a way no Linux run would have revealed.
"""

from __future__ import annotations

import ctypes
import os
import sys

import pytest

from phantom_tap import cli, keepawake
from phantom_tap.secrets import SecretsError, SecretStore


def test_key_file_is_accepted_on_windows_despite_mode_bits(tmp_path, monkeypatch):
    """Windows reports every file as 0o666; the POSIX check would reject all of them."""
    store = SecretStore(tmp_path / "k.key", tmp_path / "s.enc")
    store.set("hp_password", "hunter2")
    os.chmod(tmp_path / "k.key", 0o666)  # what st_mode looks like on Windows

    with pytest.raises(SecretsError, match="must not be group- or world-readable"):
        store.get("hp_password")  # still enforced where it means something

    monkeypatch.setattr(sys, "platform", "win32")
    assert store.get("hp_password") == "hunter2"


class FakeKernel32:
    def __init__(self, refuse: bool = False) -> None:
        self.calls: list[int] = []
        self.refuse = refuse

    def SetThreadExecutionState(self, flags: int) -> int:
        self.calls.append(flags)
        return 0 if self.refuse else 0x80000000


def fake_windows(monkeypatch, kernel32: FakeKernel32) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": kernel32})(), raising=False)


def test_keep_awake_holds_then_releases(monkeypatch):
    kernel32 = FakeKernel32()
    fake_windows(monkeypatch, kernel32)

    with keepawake.keep_awake():
        assert kernel32.calls == [keepawake.ES_CONTINUOUS | keepawake.ES_SYSTEM_REQUIRED]

    # Released, or the PC would never sleep again after the first race.
    assert kernel32.calls[-1] == keepawake.ES_CONTINUOUS


def test_keep_awake_releases_even_when_the_race_crashes(monkeypatch):
    kernel32 = FakeKernel32()
    fake_windows(monkeypatch, kernel32)

    with pytest.raises(RuntimeError), keepawake.keep_awake():
        raise RuntimeError("race blew up")

    assert kernel32.calls[-1] == keepawake.ES_CONTINUOUS


def test_keep_awake_survives_windows_refusing(monkeypatch, caplog):
    fake_windows(monkeypatch, FakeKernel32(refuse=True))
    with keepawake.keep_awake():
        pass
    assert "refused" in caplog.text


def test_keep_awake_is_a_no_op_elsewhere(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with keepawake.keep_awake():
        pass  # must not touch ctypes.windll, which does not exist here


def test_log_file_is_utf8_so_hebrew_survives(tmp_path, monkeypatch):
    """A scheduled task has no console; the log file is all there is."""
    import logging

    log = tmp_path / "logs" / "pt.log"
    monkeypatch.chdir(tmp_path)
    cli.main(["--log-file", str(log), "-v", "capture"])
    logging.getLogger("phantom_tap").info("נרשמת לבודי פאוור")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert "נרשמת לבודי פאוור" in log.read_text(encoding="utf-8")


def test_tzdata_is_declared_for_windows():
    """Without it, ZoneInfo("Asia/Jerusalem") fails on import on Windows."""
    import tomllib
    from pathlib import Path

    project = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    deps = project["project"]["dependencies"]
    assert any(d.startswith("tzdata") and "win32" in d for d in deps)

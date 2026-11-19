"""The UI fallback, against a fake device that behaves like the real screens.

What the screenshots established, and what these tests hold the fallback to:

  * before the window opens the card shows "תזכורת"; at the published minute the
    same button becomes "הרשמה" - so "תזכורת" means wait, not give up
  * a full class offers "הרשמה להמתנה" instead
  * in the seat dialog taken seats stay listed, grayed out; free ones are red
  * "המשך" stays disabled until a free seat is selected
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from phantom_tap.holmesplace import ui as uimod
from phantom_tap.holmesplace.ui import (
    BUTTON_CONTINUE,
    BUTTON_REGISTER,
    BUTTON_REMINDER,
    BUTTON_WAITLIST,
    SEAT_DIALOG,
    HolmesPlaceUi,
)

FREE, TAKEN, TAKEN_BUT_TAPPABLE = "free", "taken", "taken_but_tappable"


class FakeScreen:
    """Just enough of uiautomator2's device object for the fallback's calls."""

    def __init__(self, *, opens_at=None, full=False, seats=None, flips_without_refresh=True):
        self.opens_at = opens_at
        self.full = full
        self.flips_without_refresh = flips_without_refresh
        self.refreshed_after_open = False
        self.seats = seats or {}
        self.dialog_open = False
        self.selected = None
        self.taps: list[str] = []

    # what is on screen right now: text -> enabled
    def visible(self) -> dict[str, bool]:
        if self.dialog_open:
            out = {SEAT_DIALOG: True, BUTTON_CONTINUE: self.selected is not None}
            for number, state in self.seats.items():
                # Gray (taken) seats are still listed. Some apps disable them,
                # some only restyle them - both kinds are modelled.
                out[str(number)] = state != TAKEN
            return out
        is_open = self.opens_at is None or time.time() >= self.opens_at
        if is_open and not self.flips_without_refresh and not self.refreshed_after_open:
            is_open = False  # the card only updates after a pull-to-refresh
        if not is_open:
            return {BUTTON_REMINDER: True}
        return {BUTTON_WAITLIST: True} if self.full else {BUTTON_REGISTER: True}

    def tap(self, text: str) -> None:
        self.taps.append(text)
        if text == BUTTON_REGISTER:
            self.dialog_open = bool(self.seats)
        elif self.dialog_open and text.isdigit():
            if self.seats.get(int(text)) == FREE:
                self.selected = int(text)
        elif text == BUTTON_CONTINUE:
            self.dialog_open = False

    # --- uiautomator2 surface -------------------------------------------------
    def __call__(self, **kw):
        return _Selector(self, kw)

    def swipe_ext(self, direction, scale=0.5):
        if direction == "down" and self.opens_at and time.time() >= self.opens_at:
            self.refreshed_after_open = True

    def screenshot(self, path):
        Path(path).write_bytes(b"")


class _Selector:
    def __init__(self, screen: FakeScreen, kw: dict):
        self.screen, self.kw = screen, kw

    def _match(self) -> str | None:
        for text, enabled in self.screen.visible().items():
            if "text" in self.kw and text != self.kw["text"]:
                continue
            if "textContains" in self.kw and self.kw["textContains"] not in text:
                continue
            if self.kw.get("enabled") is True and not enabled:
                continue
            if "scrollable" in self.kw:
                return "list"
            return text
        return None

    @property
    def exists(self) -> bool:
        return self._match() is not None

    def click(self) -> None:
        self.screen.tap(self._match())

    def click_exists(self, timeout=0):
        if self.exists:
            self.click()
            return True
        return False

    def wait(self, timeout=0):
        return self.exists

    @property
    def scroll(self):
        return self

    def to(self, **kw):  # every seat is "on screen" in the fake
        return True


@pytest.fixture
def make_ui(tmp_path):
    def _make(screen: FakeScreen) -> HolmesPlaceUi:
        # Skip __init__: it connects to a real device over adb.
        app = HolmesPlaceUi.__new__(HolmesPlaceUi)
        app.d, app.shots, app.steps, app.taken = screen, tmp_path, [], []
        return app

    return _make


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(uimod, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(uimod, "REFRESH_EVERY", 0.05)


# ------------------------------------------------------------- registration --


def test_waits_for_the_button_to_flip_and_taps_it_at_once(make_ui):
    """The fallback arrives early and must sit on "תזכורת" until it becomes "הרשמה"."""
    opens_at = time.time() + 0.3
    screen = FakeScreen(opens_at=opens_at)

    result = make_ui(screen).register(wait_until=opens_at + 5)

    assert result == "registered"
    assert screen.taps == [BUTTON_REGISTER]
    assert time.time() - opens_at < 0.25, "tapped too long after the button flipped"


def test_refreshes_when_the_card_does_not_update_by_itself(make_ui):
    opens_at = time.time() + 0.2
    screen = FakeScreen(opens_at=opens_at, flips_without_refresh=False)

    assert make_ui(screen).register(wait_until=opens_at + 5) == "registered"
    assert screen.refreshed_after_open


def test_gives_up_only_after_the_deadline(make_ui):
    screen = FakeScreen(opens_at=time.time() + 60)
    started = time.time()

    assert make_ui(screen).register(wait_until=time.time() + 0.2) == "closed"
    assert 0.2 <= time.time() - started < 1.0
    assert screen.taps == []


def test_without_a_deadline_a_closed_card_is_reported_at_once(make_ui):
    screen = FakeScreen(opens_at=time.time() + 60)
    assert make_ui(screen).register() == "closed"


def test_full_class_joins_the_waitlist_only_when_allowed(make_ui):
    assert make_ui(FakeScreen(full=True)).register(join_waitlist=False) == "closed"

    screen = FakeScreen(full=True)
    assert make_ui(screen).register(join_waitlist=True) == "waitlisted"
    assert screen.taps == [BUTTON_WAITLIST]


def test_register_never_taps_the_waitlist_button_by_text_overlap(make_ui):
    """"הרשמה" is a prefix of "הרשמה להמתנה" - an exact match keeps them apart."""
    screen = FakeScreen(full=True)
    make_ui(screen).register(join_waitlist=False)
    assert BUTTON_WAITLIST not in screen.taps


# --------------------------------------------------------------------- seats --


def open_dialog(screen: FakeScreen) -> FakeScreen:
    screen.dialog_open = True
    return screen


def test_takes_the_first_free_preference(make_ui):
    screen = open_dialog(FakeScreen(seats={1: TAKEN, 2: FREE, 3: TAKEN, 4: FREE}))
    assert make_ui(screen).choose_seat((3, 4, 2)) == 4  # 3 is gray, 4 is red


def test_a_gray_seat_is_never_tapped(make_ui):
    screen = open_dialog(FakeScreen(seats={1: TAKEN, 2: FREE}))
    make_ui(screen).choose_seat((1, 2))
    assert "1" not in screen.taps


def test_a_taken_seat_that_is_still_tappable_is_detected_by_the_confirm_button(make_ui):
    """If the app only restyles taken seats, "המשך" staying disabled gives it away."""
    screen = open_dialog(FakeScreen(seats={5: TAKEN_BUT_TAPPABLE, 6: FREE}))
    assert make_ui(screen).choose_seat((5, 6)) == 6


def test_no_preference_free_falls_back_to_the_first_free_seat(make_ui):
    seats = {n: TAKEN for n in range(1, 10)} | {7: FREE}
    screen = open_dialog(FakeScreen(seats=seats))
    assert make_ui(screen).choose_seat((1, 2)) == 7


def test_every_seat_taken_returns_none(make_ui):
    screen = open_dialog(FakeScreen(seats={n: TAKEN for n in range(1, 6)}))
    assert make_ui(screen).choose_seat((1,)) is None


def test_matches_the_screenshot(make_ui):
    """Your screenshot: of seats 1-9, only 2 and 4 are red."""
    seats = {n: (FREE if n in (2, 4) else TAKEN) for n in range(1, 10)}
    screen = open_dialog(FakeScreen(seats=seats))
    assert make_ui(screen).choose_seat((12, 4, 2)) == 4

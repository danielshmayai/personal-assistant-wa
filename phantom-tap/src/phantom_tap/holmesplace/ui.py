"""The fallback: drive the real app on a real device.

This path exists because the HTTP path can break without warning - the backend
changes a field, or a new app version starts behaving differently - and a broken
protocol should cost you a good seat, not the class.

It is honestly slower. Waking the device, launching the app, finding the class
and tapping through the seat dialog takes seconds, not milliseconds, so it loses
any contested class. That is the trade: a late booking beats no booking, and for
an uncontested class it is indistinguishable from the fast path.

Selectors come from the app's own Hebrew UI: the "שיעורי סטודיו" tab, the day
strip, a class card carrying the class name, the "הרשמה" button, and the
"בחירת מקום" seat dialog with its "המשך" confirm.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger("phantom_tap.ui")

PACKAGE = "com.holmesplace"

TAB_STUDIO = "שיעורי סטודיו"
BUTTON_REGISTER = "הרשמה"
BUTTON_CONTINUE = "המשך"
SEAT_DIALOG = "בחירת מקום"
BUTTON_REMINDER = "תזכורת"  # shown instead of הרשמה before registration opens
BUTTON_WAITLIST = "הרשמה להמתנה"  # shown instead of הרשמה when the class is full

POLL_INTERVAL = 0.1  # how often to look at the card while waiting for it to open
REFRESH_EVERY = 3.0  # how often to pull-to-refresh in case the card never updates itself


class UiUnavailable(RuntimeError):
    """No device, or uiautomator2 is not installed."""


@dataclass
class UiResult:
    registered: bool
    seat: int | None
    steps: list[str]
    screenshots: list[Path]
    reason: str = ""


class HolmesPlaceUi:
    def __init__(self, serial: str | None = None, shots: Path = Path("screenshots")) -> None:
        try:
            import uiautomator2
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise UiUnavailable(
                "uiautomator2 is not installed. Install the device extra: "
                "pip install -e '.[device]'"
            ) from exc
        try:
            self.d = uiautomator2.connect(serial)
        except Exception as exc:
            raise UiUnavailable(f"cannot reach the device over adb: {exc}") from exc
        self.shots = shots
        self.shots.mkdir(parents=True, exist_ok=True)
        self.steps: list[str] = []
        self.taken: list[Path] = []

    # Every step is photographed. When this path fails at 14:10 nobody is
    # watching the screen, and a screenshot is the only way to find out why.
    def _step(self, label: str) -> None:
        self.steps.append(label)
        path = self.shots / f"{time.strftime('%Y%m%d-%H%M%S')}-{len(self.steps):02d}-{label}.png"
        try:
            self.d.screenshot(str(path))
            self.taken.append(path)
        except Exception as exc:
            logger.debug("screenshot failed at %s: %s", label, exc)
        logger.info("ui: %s", label)

    def open_app(self) -> None:
        self.d.screen_on()
        self.d.app_start(PACKAGE, stop=False)
        self.d.wait_activity(".*", timeout=15)
        self._step("app-open")

    def go_to_schedule(self) -> None:
        self.d(text=TAB_STUDIO).click_exists(timeout=10)
        self._step("schedule-tab")

    def pick_day(self, day: date) -> bool:
        """The day strip labels days as d.M - "16.9" for 16 September."""
        label = f"{day.day}.{day.month}"
        chip = self.d(text=label)
        for _ in range(8):
            if chip.exists:
                chip.click()
                self._step(f"day-{label}")
                return True
            # The strip scrolls right-to-left; swipe to reach later days.
            self.d.swipe_ext("left", scale=0.6)
        self._step(f"day-{label}-not-found")
        return False

    def find_class(self, class_name: str, start_hhmm: str) -> bool:
        card = self.d(textContains=class_name)
        for _ in range(12):
            if card.exists:
                self._step("class-found")
                return True
            self.d.swipe_ext("up", scale=0.7)
        self._step("class-not-found")
        return False

    def register(self, *, join_waitlist: bool = False, wait_until: float | None = None) -> str:
        """Tap the card's action. Returns "registered", "waitlisted" or "closed".

        Before the window opens the card offers "תזכורת"; at the published minute
        the same button becomes "הרשמה". So "תזכורת" is not a refusal, it is a
        countdown: with `wait_until` (a unix deadline, normally T0 plus a margin)
        we keep watching the card and tap the moment it flips.
        """
        while True:
            # Exact text match: "הרשמה" must not match "הרשמה להמתנה".
            if self.d(text=BUTTON_REGISTER).click_exists(timeout=0.3):
                self._step("register-tapped")
                return "registered"
            if join_waitlist and self.d(text=BUTTON_WAITLIST).click_exists(timeout=0.3):
                self._step("waitlist-tapped")
                return "waitlisted"
            if wait_until is None or time.time() >= wait_until:
                break
            if self.d(text=BUTTON_REMINDER).exists:
                self._refresh_if_stale()
            time.sleep(POLL_INTERVAL)

        self._step("still-closed" if self.d(text=BUTTON_REMINDER).exists else "no-register-button")
        return "closed"

    def _refresh_if_stale(self) -> None:
        """Pull to refresh every few seconds while waiting.

        Whether the app flips the button by itself at the minute or only on a
        reload is not known yet, so do both: watch continuously, and reload
        periodically so a card that never updates on its own still flips.
        """
        now = time.monotonic()
        if now - getattr(self, "_last_refresh", 0.0) < REFRESH_EVERY:
            return
        self._last_refresh = now
        self.d.swipe_ext("down", scale=0.5)

    def choose_seat(self, preferred: tuple[int, ...]) -> int | None:
        """The seat dialog: a floor map, then a scrollable list of numbers.

        Taken seats stay in the list - grayed out - and free ones are outlined in
        red. So a seat number being on screen says nothing about whether it can be
        had. What the dialog does say reliably is "המשך": it stays disabled until a
        free seat is selected. A seat counts as claimed only once the confirm
        button has become tappable, whatever styling the app uses for taken seats.
        """
        if not self.d(textContains=SEAT_DIALOG).wait(timeout=8):
            self._step("no-seat-dialog")  # this class has no seat selection
            return None

        for seat in preferred:
            if self._try_seat(seat):
                return seat

        # Nothing preferred was free: walk the list top to bottom and take the
        # first seat the dialog actually accepts.
        for number in range(1, 100):
            if not self.d(text=str(number)).exists:
                self.d(scrollable=True).scroll.to(text=str(number))
                if not self.d(text=str(number)).exists:
                    break  # past the last seat
            if self._try_seat(number):
                return number
        self._step("no-free-seat")
        return None

    def _try_seat(self, seat: int) -> bool:
        entry = self.d(text=str(seat), enabled=True)
        if not entry.exists:
            self.d(scrollable=True).scroll.to(text=str(seat))
            entry = self.d(text=str(seat), enabled=True)
        if not entry.exists:
            return False  # disabled: taken (gray)
        entry.click()
        confirm = self.d(text=BUTTON_CONTINUE, enabled=True)
        if not confirm.wait(timeout=1.5):
            return False  # tapping it changed nothing: taken after all
        confirm.click()
        self._step(f"seat-{seat}")
        return True

    def book(
        self,
        day: date,
        class_name: str,
        start_hhmm: str,
        preferred: tuple[int, ...],
        *,
        join_waitlist: bool = False,
        opens_at: float | None = None,
    ) -> UiResult:
        def done(registered: bool, seat: int | None, reason: str = "") -> UiResult:
            return UiResult(registered, seat, self.steps, self.taken, reason)

        self.open_app()
        self.go_to_schedule()
        if not self.pick_day(day):
            return done(False, None, f"the day strip never showed {day.day}.{day.month}")
        if not self.find_class(class_name, start_hhmm):
            return done(False, None, f"{class_name} at {start_hhmm} was not on that day's list")
        # Wait past T0 by a margin: the fallback reaches the card early on purpose
        # (navigating takes seconds) and must not give up while the button still
        # reads "תזכורת".
        deadline = opens_at + 90 if opens_at else None
        action = self.register(join_waitlist=join_waitlist, wait_until=deadline)
        if action == "closed":
            return done(False, None, "the register button was not offered")
        if action == "waitlisted":
            return done(False, None, "class full - joined the waitlist")
        return done(True, self.choose_seat(preferred))

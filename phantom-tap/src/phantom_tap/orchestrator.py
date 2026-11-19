"""From "I want that class on Wednesday" to a confirmed booking.

The loop for one watched class:

    find the next date it runs
        -> read the class off the schedule, and with it the server's own
           registration-open moment
        -> sleep until T0 minus the lead-in
        -> log in fresh, warm the connection, re-resolve the class id
        -> race
        -> verify, record, notify
        -> go round again for next week

Each watch runs as its own task with its own client and its own connection. Two
classes can open at the same second - a shared client would serialise them on one
connection and hand the second one a loss it did not have to take.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import date, datetime, timedelta

from phantom_tap import clock as clockmod
from phantom_tap.clock import Clock, UnsyncedClock
from phantom_tap.config import Config, Watch
from phantom_tap.holmesplace.api import HolmesPlaceClient, Outcome
from phantom_tap.holmesplace.models import (
    EXPECTED_LEAD,
    ISRAEL,
    ClassSlot,
    Credentials,
    Endpoints,
    ProtocolError,
)
from phantom_tap.keepawake import keep_awake
from phantom_tap.notify import Notifier, format_result
from phantom_tap.race import RacePlan, RaceResult, race
from phantom_tap.secrets import SecretStore
from phantom_tap.store import Store

logger = logging.getLogger("phantom_tap.orchestrator")

# How long before T0 we stop polling and commit to the wait.
RESYNC_BEFORE_T0 = 120.0
# The UI fallback opens the app this long before T0: launching it, picking the day
# and scrolling to the card takes seconds, and the card then waits for the flip.
UI_ARRIVE_EARLY = 60.0


class HttpPathBroken(ProtocolError):
    """The direct path failed in a way the app itself might not. Carries T0."""

    def __init__(self, message: str, *, opens_at: float) -> None:
        super().__init__(message)
        self.opens_at = opens_at


def expected_open(watch: Watch, day: date) -> float:
    """When registration should open, by the rule every observed class followed.

    Only used when the server cannot be asked - the HTTP path is broken - which is
    exactly when the UI fallback runs.
    """
    start = datetime.combine(day, watch.start_time, tzinfo=ISRAEL)
    return (start - EXPECTED_LEAD).timestamp()
# When the class is not on the schedule yet, look again this often.
DISCOVERY_INTERVAL = 30 * 60.0


class NotYetPublished(LookupError):
    """The club has not put this class on the schedule yet. Not an error."""


def credentials(config: Config) -> Credentials:
    store = SecretStore(config.key_path, config.secrets_path)
    return Credentials(
        username=config.username,
        password=store.get("hp_password", env_fallback="HP_PASSWORD"),
        club_id=config.club_id,
    )


def make_client(config: Config, endpoints: Endpoints) -> HolmesPlaceClient:
    return HolmesPlaceClient(endpoints, credentials(config))


async def resolve(client: HolmesPlaceClient, watch: Watch, day: date) -> ClassSlot:
    """Find the class, or say why we cannot yet."""
    slot = await client.find_class(day, watch.class_name, watch.start_hhmm)
    if slot is None:
        raise NotYetPublished(
            f"{watch.label()} is not on the {day.isoformat()} schedule yet"
        )
    if slot.opens_at and not slot.lead_matches_expectation:
        # Not fatal - the server is authoritative. But the 5h rule held for every
        # class we observed, so a break in it means something changed and the
        # next silent loss would be unexplained.
        logger.warning(
            "%s: registration opens %s, which is not start-minus-5h. Trusting the "
            "server, but the rule changed.",
            watch.label(),
            slot.opens_at.astimezone(ISRAEL).isoformat(timespec="minutes"),
        )
    return slot


def plan_for(watch: Watch, slot: ClassSlot) -> RacePlan:
    if slot.opens_at is None:
        raise NotYetPublished(
            f"{watch.label()} carries no registration-open time. Either registration "
            f"is already open, or the field map in endpoints.json is wrong "
            f"(re-run `pt analyze`)."
        )
    return RacePlan.from_slot(
        slot,
        preferred_spots=watch.preferred_spots,
        seat_fallback=watch.seat_fallback,
        join_waitlist=watch.join_waitlist,
    )


async def book(
    config: Config,
    endpoints: Endpoints,
    watch: Watch,
    *,
    store: Store,
    notifier: Notifier,
    live: bool,
    day: date | None = None,
) -> RaceResult:
    """Chase one class through one registration window.

    Direct HTTP first. If that path is broken - the protocol changed, or the server
    refuses it outright - and `[device] ui_fallback` is on, the same class is
    booked by driving the app on the phone instead. Slower, so it loses contested
    classes, but a broken protocol then costs a good seat rather than the class.
    """
    day = day or watch.next_date()
    try:
        return await _book_http(
            config, endpoints, watch, store=store, notifier=notifier, live=live, day=day
        )
    except ProtocolError as exc:
        if not (live and config.device.ui_fallback):
            raise
        opens_at = getattr(exc, "opens_at", None) or expected_open(watch, day)
        logger.error("direct path broken (%s) - falling back to the app on the phone", exc)
        return await _book_via_app(
            config, watch, day, opens_at=opens_at, store=store, notifier=notifier, why=str(exc)
        )


async def _book_via_app(
    config: Config,
    watch: Watch,
    day: date,
    *,
    opens_at: float,
    store: Store,
    notifier: Notifier,
    why: str,
) -> RaceResult:
    from phantom_tap.holmesplace import ui as uimod

    arrive_in = opens_at - UI_ARRIVE_EARLY - time.time()
    if arrive_in > 0:
        logger.info("UI fallback: opening the app %s from now", _human(arrive_in))
        await asyncio.sleep(arrive_in)

    def run() -> uimod.UiResult:
        app = uimod.HolmesPlaceUi(config.device.serial or None, shots=config.db_path.parent / "screenshots")
        return app.book(
            day,
            watch.class_name,
            watch.start_hhmm,
            watch.preferred_spots,
            join_waitlist=watch.join_waitlist,
            opens_at=opens_at,
        )

    with keep_awake():
        try:
            ui = await asyncio.to_thread(run)  # uiautomator2 is blocking
            # Not verified: the only independent check is the HTTP path, which is
            # the thing that is broken. The screenshots are the evidence.
            result = RaceResult(
                registered=ui.registered,
                waitlisted=ui.waitlisted,
                seat=ui.seat,
                verified=False,
                reason=f"via the app on the phone, check the screenshots ({ui.reason or 'ok'})",
            )
        except uimod.UiUnavailable as exc:
            result = RaceResult(reason=f"direct path broken ({why}) and the phone fallback could not start: {exc}")

    plan = RacePlan(
        # Per occurrence, so next week's run is not mistaken for this one.
        class_id=f"ui:{day.isoformat()}:{watch.label()}",
        t0=opens_at,
        label=f"{watch.label()} {day:%d/%m}",
    )
    store.record(plan=plan, result=result, clock_offset=0.0, clock_disp=0.0)
    logger.info("%s: %s", plan.label, result.summary())
    await notifier.send(format_result(plan.label, result, overshoot_ms=0.0, offset_ms=0.0))
    return result


async def _book_http(
    config: Config,
    endpoints: Endpoints,
    watch: Watch,
    *,
    store: Store,
    notifier: Notifier,
    live: bool,
    day: date,
) -> RaceResult:
    client = make_client(config, endpoints)
    awake = contextlib.ExitStack()
    try:
        await client.login()
        slot = await resolve(client, watch, day)

        if slot.booked or store.already_won(slot.id):
            logger.info("%s is already booked - nothing to race for", slot.describe())
            return RaceResult(registered=True, verified=True, reason="already booked")

        plan = plan_for(watch, slot)
        wait = plan.t0 - datetime.now(tz=ISRAEL).timestamp()
        logger.info(
            "%s: registration opens %s (%s from now)",
            slot.describe(),
            slot.opens_at.astimezone(ISRAEL).strftime("%d/%m %H:%M:%S"),
            _human(wait),
        )

        if wait > config.lead_in_seconds:
            await asyncio.sleep(wait - config.lead_in_seconds)
            # The token from before the sleep may be minutes old and the class id
            # can be reissued when the club edits the schedule. Both are cheap to
            # redo and expensive to get wrong.
            await client.login()
            slot = await resolve(client, watch, day)
            plan = plan_for(watch, slot)

        # From here to the end of the race the machine must not sleep. Held only
        # now, not during the long wait above, so the PC sleeps normally all week.
        awake.enter_context(keep_awake())
        synced = _sync_clock()
        race_clock = Clock(synced)
        await client.prewarm()

        if not live:
            logger.info(
                "DRY RUN - would fire at %s for class %s, seats %s. Nothing was sent.",
                datetime.fromtimestamp(plan.t0, tz=ISRAEL).strftime("%H:%M:%S"),
                plan.class_id,
                plan.preferred_spots or "(any)",
            )
            return RaceResult(reason="dry run - no request was sent")

        result = await race(client, plan, race_clock)
        store.record(
            plan=plan,
            result=result,
            clock_offset=race_clock.offset,
            clock_disp=synced.dispersion if synced else 0.0,
        )
        refused = bool(result.attempts) and result.attempts[-1].outcome is Outcome.FATAL
        if refused and not result.registered and config.device.ui_fallback:
            # Recorded above for the post-mortem; the fallback reports the outcome.
            raise HttpPathBroken(result.reason, opens_at=plan.t0)
        logger.info("%s: %s", plan.label, result.summary())
        await notifier.send(
            format_result(
                plan.label,
                result,
                overshoot_ms=result.max_overshoot_ms,
                offset_ms=race_clock.offset * 1000,
            )
        )
        return result
    finally:
        awake.close()
        await client.aclose()


async def watch_forever(
    config: Config, endpoints: Endpoints, watch: Watch, *, store: Store, notifier: Notifier
) -> None:
    """One watch, week after week, surviving everything that is not a bug."""
    while True:
        try:
            result = await book(
                config, endpoints, watch, store=store, notifier=notifier, live=True
            )
            # Move past today so next week's occurrence is the one we find.
            await asyncio.sleep(_until_tomorrow())
            if not result.registered:
                logger.info("%s: will try again next week", watch.label())
        except NotYetPublished as exc:
            logger.info("%s - checking again in %s", exc, _human(DISCOVERY_INTERVAL))
            await asyncio.sleep(DISCOVERY_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A crash in one watch must not take the others down with it.
            logger.exception("%s: unhandled error, retrying in 10 minutes", watch.label())
            await asyncio.sleep(600)


async def daemon(config: Config, endpoints: Endpoints) -> None:
    from phantom_tap.notify import build as build_notifier

    store = Store(config.db_path)
    notifier = build_notifier(config.notify)
    watches = config.enabled_watches()
    logger.info("daemon up with %d watch(es): %s", len(watches), ", ".join(w.label() for w in watches))
    try:
        async with asyncio.TaskGroup() as group:
            for watch in watches:
                group.create_task(
                    watch_forever(config, endpoints, watch, store=store, notifier=notifier),
                    name=watch.label(),
                )
    finally:
        store.close()


def _sync_clock():
    """Sync, and be explicit when the result is not good enough to race on."""
    try:
        synced = clockmod.sync()
    except UnsyncedClock as exc:
        logger.error("%s - racing on the local clock, which may be seconds out", exc)
        return None
    if not synced.trustworthy:
        logger.warning(
            "NTP servers disagree by %.0fms, which is wider than the window we are "
            "racing for. Proceeding, but a loss here is not diagnostic.",
            synced.dispersion * 1000,
        )
    return synced


def _until_tomorrow() -> float:
    now = datetime.now(tz=ISRAEL)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return (midnight - now).total_seconds()


def _human(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"

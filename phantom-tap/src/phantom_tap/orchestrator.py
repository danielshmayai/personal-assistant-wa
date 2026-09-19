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
import logging
from datetime import date, datetime, timedelta

from phantom_tap import clock as clockmod
from phantom_tap.clock import Clock, UnsyncedClock
from phantom_tap.config import Config, Watch
from phantom_tap.holmesplace.api import HolmesPlaceClient
from phantom_tap.holmesplace.models import ISRAEL, ClassSlot, Credentials, Endpoints
from phantom_tap.notify import Notifier, format_result
from phantom_tap.race import RacePlan, RaceResult, race
from phantom_tap.secrets import SecretStore
from phantom_tap.store import Store

logger = logging.getLogger("phantom_tap.orchestrator")

# How long before T0 we stop polling and commit to the wait.
RESYNC_BEFORE_T0 = 120.0
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
    """Chase one class through one registration window."""
    day = day or watch.next_date()
    client = make_client(config, endpoints)
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

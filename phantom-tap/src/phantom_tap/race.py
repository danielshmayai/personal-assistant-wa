"""The race: claim the registration, then claim the seat.

This is the only module with hard real-time behaviour, and it is deliberately the
smallest one. Three rules govern it.

**Nothing is computed at T0.** Every request the race can possibly send is built
during the lead-in, one per burst slot, so firing is a socket write and nothing
else. Building a request costs hundreds of microseconds; at T0 that is the margin.

**The POST is never believed.** A booking that exists only in a 200 response is
not a booking you can turn up to. Every win is confirmed by an independent read.

**Registered-but-seatless is a win.** Losing the preferred seat is a disappointment;
re-firing phase 1 to chase a better one risks cancelling the registration already
won. The race degrades toward "you are in the class", never away from it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from phantom_tap.clock import Clock
from phantom_tap.holmesplace.api import Attempt, HolmesPlaceClient, Outcome
from phantom_tap.holmesplace.models import Seat

logger = logging.getLogger("phantom_tap.race")

# Attempt offsets from T0, in seconds. The first four cover a server that opens
# the window a beat late or drops a packet; the last two cover someone else's
# cancellation landing back in the pool.
DEFAULT_BURST = (0.0, 0.12, 0.35, 0.8, 1.5, 3.0)

# The app publishes the opening moment to the minute - "פתיחת הרשמה: 16/09 14:10" -
# and the API field behind it is formatted the same way. So when the seconds are
# zero we do not actually know whether the window opens at 14:10:00 or 14:10:47;
# we only know it opens somewhere inside that minute. Firing a three-second burst
# at 14:10:00 and giving up would lose every class that opens a few seconds in.
COARSE_T0_PRECISION = 60.0
# Dense while the likely case (exactly on the minute) plays out, then patient.
_DENSE_UNTIL, _DENSE_STEP, _PATIENT_STEP = 6.0, 0.25, 1.0


def burst_for(precision: float) -> tuple[float, ...]:
    """Attempt offsets appropriate to how precisely T0 is known.

    A precise T0 gets the tight burst and nothing more - extra requests against a
    server that already answered are just noise. A T0 known only to the minute gets
    the tight burst *and* a polling tail that covers the rest of that minute.
    """
    if precision <= 1.0:
        return DEFAULT_BURST

    offsets = list(DEFAULT_BURST)
    t = _DENSE_UNTIL
    while (last := offsets[-1]) < _DENSE_UNTIL:
        offsets.append(round(last + _DENSE_STEP, 3))
    t = offsets[-1]
    while t < precision + 2.0:
        t = round(t + _PATIENT_STEP, 3)
        offsets.append(t)
    return tuple(offsets)

# Seat claims are a separate, tighter burst: the hold between phases is short and
# undocumented, so hesitating costs the seat.
SEAT_BURST = (0.0, 0.15, 0.4)


@dataclass(frozen=True)
class RacePlan:
    class_id: str
    t0: float  # unix seconds, read from the server, never typed by a human
    preferred_spots: tuple[int, ...] = ()
    seat_fallback: str = "nearest"  # nearest | any | none
    burst: tuple[float, ...] = DEFAULT_BURST
    label: str = ""
    #: How precisely T0 is known, in seconds. 1.0 when the server gave us seconds,
    #: 60.0 when it gave us only a minute. See `burst_for`.
    t0_precision: float = 1.0

    @classmethod
    def from_slot(cls, slot, **kw) -> RacePlan:
        """Build a plan from a schedule entry, deriving the burst from its precision."""
        precision = (
            COARSE_T0_PRECISION
            if slot.opens_at.second == 0 and slot.opens_at.microsecond == 0
            else 1.0
        )
        return cls(
            class_id=slot.id,
            t0=slot.opens_at.timestamp(),
            burst=burst_for(precision),
            t0_precision=precision,
            label=slot.describe(),
            **kw,
        )


@dataclass
class RaceResult:
    registered: bool = False
    seat: int | None = None
    verified: bool = False
    reason: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    max_overshoot_ms: float = 0.0

    @property
    def won(self) -> bool:
        return self.registered and self.verified

    def summary(self) -> str:
        if self.won:
            seat = f", seat {self.seat}" if self.seat is not None else ""
            first = next((a for a in self.attempts if a.outcome in (Outcome.OK, Outcome.ALREADY)), None)
            when = f" at T0+{first.fired_at * 1000:.0f}ms" if first else ""
            return f"booked{seat}{when}"
        if self.registered and not self.verified:
            return f"registered but NOT confirmed by an independent read - {self.reason}"
        return f"not booked - {self.reason}"


async def race(client: HolmesPlaceClient, plan: RacePlan, clock: Clock) -> RaceResult:
    """Run both phases. Assumes the client is authenticated and warm."""
    result = RaceResult()

    # Built now, before the wait, so T0 costs nothing. One per burst slot: an
    # httpx.Request is cheap, and reusing one across sends risks a consumed stream.
    requests = [client.prepare_register(plan.class_id) for _ in plan.burst]

    seats: list[Seat] = []
    if plan.preferred_spots or plan.seat_fallback != "none":
        try:
            seats = await client.seat_map(plan.class_id)
            logger.info("seat map cached: %d seats, %d free right now", len(seats), sum(s.available for s in seats))
        except Exception as exc:
            logger.warning("seat map unavailable before T0 (%s); will read it after registering", exc)

    logger.info(
        "armed: class %s, T0 in %.1fs, %d attempts over %.0fs (T0 known to ±%.0fs)",
        plan.class_id,
        plan.t0 - clock.now(),
        len(plan.burst),
        plan.burst[-1],
        plan.t0_precision,
    )

    # ---------------------------------------------------------- phase 1 -----
    for offset, request in zip(plan.burst, requests, strict=True):
        target = plan.t0 + offset
        # A T0 published to the minute can already be in the past when we arrive.
        # Firing immediately is right, but that lateness is not scheduler jitter and
        # folding it into the health metric would hide a genuinely loaded machine.
        was_scheduled = target > clock.now()
        overshoot = clock.sleep_until(target)
        if was_scheduled:
            result.max_overshoot_ms = max(result.max_overshoot_ms, overshoot * 1000)

        fired_at = clock.now() - plan.t0
        started = time.monotonic()
        outcome, response, detail = await client.send(request, expect=client.ep.register)
        attempt = Attempt(
            outcome=outcome,
            status=response.status_code if response else None,
            latency=time.monotonic() - started,
            fired_at=fired_at,
            detail=detail,
        )
        result.attempts.append(attempt)
        logger.info(
            "attempt %d: T0%+.0fms -> %s in %.0fms %s",
            len(result.attempts),
            fired_at * 1000,
            outcome.value,
            attempt.latency * 1000,
            detail,
        )

        if outcome in (Outcome.OK, Outcome.ALREADY):
            result.registered = True
            if response is not None and outcome is Outcome.OK:
                seats = _merge_seats(seats, client._seats(_safe_json(response), client.ep.seat_map)) \
                    if client.ep.seat_map else seats
            break
        if outcome is Outcome.FULL:
            result.reason = f"class full ({detail})"
            return result
        if outcome is Outcome.FATAL:
            result.reason = f"fatal: {detail}"
            return result
        # NOT_OPEN and RETRY both mean: wait for the next slot.
    else:
        last = result.attempts[-1] if result.attempts else None
        result.reason = f"exhausted {len(plan.burst)} attempts; last was {last.outcome.value if last else 'none'}"
        return result

    # ---------------------------------------------------------- phase 2 -----
    if client.ep.confirm_seat is not None:
        result.seat = await _claim_seat(client, plan, seats, result)

    # ---------------------------------------------------------- verify ------
    try:
        result.verified = await client.verify_booked(plan.class_id)
    except Exception as exc:
        result.reason = f"registered, but the confirmation read failed: {exc}"
        return result

    result.reason = "" if result.verified else "server does not list the booking after registering"
    return result


async def _claim_seat(
    client: HolmesPlaceClient, plan: RacePlan, seats: list[Seat], result: RaceResult
) -> int | None:
    if not seats:
        try:
            seats = await client.seat_map(plan.class_id)
        except Exception as exc:
            logger.warning("no seat map after registering (%s) - keeping the registration", exc)
            return None

    for seat in candidate_seats(seats, plan.preferred_spots, plan.seat_fallback):
        for offset in SEAT_BURST:
            if offset:
                await asyncio.sleep(offset)
            started = time.monotonic()
            request = client.prepare_seat(plan.class_id, seat)
            outcome, response, detail = await client.send(request, expect=client.ep.confirm_seat)
            result.attempts.append(
                Attempt(
                    outcome=outcome,
                    status=response.status_code if response else None,
                    latency=time.monotonic() - started,
                    fired_at=client_now_offset(plan),
                    detail=f"seat {seat}: {detail}",
                )
            )
            if outcome in (Outcome.OK, Outcome.ALREADY):
                logger.info("seat %d claimed", seat)
                return seat
            if outcome is Outcome.SEAT_TAKEN:
                logger.info("seat %d taken, moving to the next preference", seat)
                break  # next seat, not another try at this one
            if outcome is Outcome.FATAL:
                logger.warning("seat claim refused fatally (%s) - keeping the registration", detail)
                return None
            # RETRY / NOT_OPEN: try this same seat once more.

    logger.warning("registered, but no seat could be claimed")
    return None


def candidate_seats(
    seats: list[Seat], preferred: tuple[int, ...], fallback: str
) -> list[int]:
    """Seats to try, best first.

    Preferences come first, in the order given. What happens after they are all
    taken is the `fallback`:

    - `nearest`: free seats sorted by numeric distance to the closest preference.
      On this studio's floor map the numbers run along rows (1-4, then 5-10, then
      11-22 back along the next row), so numeric distance approximates physical
      distance within a row well and across rows only roughly - good enough to
      beat an arbitrary pick, and not claimed to be more than that.
    - `any`: first free seat.
    - `none`: take a preference or nothing.
    """
    free = {s.number for s in seats if s.available}
    # No seat map at all: trust the preferences and let the server referee.
    ordered = [n for n in preferred if not seats or n in free]

    if fallback == "none":
        return ordered

    rest = sorted(free - set(preferred))
    if fallback == "nearest" and preferred:
        rest.sort(key=lambda n: (min(abs(n - p) for p in preferred), n))
    return ordered + rest


def client_now_offset(plan: RacePlan) -> float:
    return time.time() - plan.t0


def _merge_seats(existing: list[Seat], fresh: list[Seat]) -> list[Seat]:
    """Prefer the fresher availability, keep anything only the cache knows about."""
    if not fresh:
        return existing
    seen = {s.number for s in fresh}
    return fresh + [s for s in existing if s.number not in seen]


def _safe_json(response):  # type: ignore[no-untyped-def]
    try:
        return response.json()
    except ValueError:
        return {}

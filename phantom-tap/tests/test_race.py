"""The race, against a backend that behaves like the real one."""

from __future__ import annotations

import time

import pytest

from phantom_tap.holmesplace.api import Outcome
from phantom_tap.race import RacePlan, race
from tests.fake_holmesplace import CLASS_ID


def plan(backend, **kw) -> RacePlan:
    return RacePlan(class_id=CLASS_ID, t0=backend.t0, **kw)


async def test_wins_and_takes_the_preferred_seat(make_backend, make_client, clock):
    backend = make_backend(opens_in=0.2)
    client = await make_client(backend)

    result = await race(client, plan(backend, preferred_spots=(12, 13, 11)), clock)
    await client.aclose()

    assert result.won
    assert result.seat == 12
    assert backend.register_calls == 1, "a won race must not fire a second registration"
    assert backend.my_seat == 12


async def test_does_not_fire_before_t0(make_backend, make_client, clock):
    """The first attempt must land at T0, not before it."""
    backend = make_backend(opens_in=0.3)
    client = await make_client(backend)

    result = await race(client, plan(backend), clock)
    await client.aclose()

    assert result.registered
    first = result.attempts[0]
    assert first.fired_at >= 0, f"fired {first.fired_at * 1000:.1f}ms early"
    assert first.fired_at < 0.05, f"fired {first.fired_at * 1000:.1f}ms late"


async def test_retries_when_the_server_opens_late(make_backend, make_client, clock):
    """A server that lags our T0 costs retries, not the booking."""
    backend = make_backend(opens_in=0.45)
    # We believe registration opens 300ms before the backend actually opens it.
    late = plan(backend)
    early = RacePlan(class_id=CLASS_ID, t0=backend.t0 - 0.3, preferred_spots=(5,))

    result = await race(client := await make_client(backend), early, clock)
    await client.aclose()

    assert result.won, result.reason
    assert result.attempts[0].outcome is Outcome.NOT_OPEN
    assert any(a.outcome is Outcome.OK for a in result.attempts)
    assert late.t0 > early.t0


async def test_transient_5xx_is_retried(make_backend, make_client, clock):
    backend = make_backend(opens_in=0.15, fail_first_n=2)
    client = await make_client(backend)

    result = await race(client, plan(backend, preferred_spots=(3,)), clock)
    await client.aclose()

    assert result.won, result.reason
    assert [a.outcome for a in result.attempts[:2]] == [Outcome.RETRY, Outcome.RETRY]
    assert result.seat == 3


async def test_full_class_stops_immediately(make_backend, make_client, clock):
    backend = make_backend(opens_in=0.15, registered=45)
    client = await make_client(backend)

    result = await race(client, plan(backend), clock)
    await client.aclose()

    assert not result.registered
    assert "full" in result.reason.lower()
    assert backend.register_calls == 1, "a full class must not be retried"


async def test_taken_preference_falls_through_to_the_next(make_backend, make_client, clock):
    backend = make_backend(opens_in=0.15, taken_seats={12, 13})
    client = await make_client(backend)

    result = await race(client, plan(backend, preferred_spots=(12, 13, 11)), clock)
    await client.aclose()

    assert result.won
    assert result.seat == 11
    assert 12 not in backend.seat_calls, "a seat known to be taken is not worth a request"


async def test_registration_is_kept_when_every_seat_is_gone(make_backend, make_client, clock):
    """Registered-but-seatless is a win. It must never be undone chasing a seat."""
    backend = make_backend(opens_in=0.15, taken_seats=set(range(1, 49)))
    client = await make_client(backend)

    result = await race(client, plan(backend, preferred_spots=(12,)), clock)
    await client.aclose()

    assert result.registered and result.verified
    assert result.seat is None
    assert backend.register_calls == 1


async def test_seat_stolen_between_phases(make_backend, make_client, clock):
    """The seat map said free; by the time we claimed it, it was not."""
    backend = make_backend(opens_in=0.15)
    client = await make_client(backend)
    original = backend._claim_seat

    def steal(seat: int):
        if seat == 12:
            backend.taken_seats.add(12)  # someone else got there first
        return original(seat)

    backend._claim_seat = steal

    result = await race(client, plan(backend, preferred_spots=(12, 13)), clock)
    await client.aclose()

    assert result.won
    assert result.seat == 13
    assert backend.seat_calls[0] == 12


async def test_already_registered_counts_as_a_win(make_backend, make_client, clock):
    backend = make_backend(opens_in=0.15)
    backend.booked = True  # a previous run, or the app on the phone, already did it
    client = await make_client(backend)

    result = await race(client, plan(backend), clock)
    await client.aclose()

    assert result.registered and result.verified
    assert result.attempts[0].outcome is Outcome.ALREADY


async def test_verification_is_independent_of_the_post(make_backend, make_client, clock):
    """A 200 that did not actually book must not be reported as a booking."""
    backend = make_backend(opens_in=0.15)
    client = await make_client(backend)
    real_register = backend._register

    def lying_register():
        response = real_register()
        backend.booked = False  # the POST said yes; the booking did not stick
        return response

    backend._register = lying_register

    result = await race(client, plan(backend, seat_fallback="none"), clock)
    await client.aclose()

    assert result.registered
    assert not result.verified
    assert not result.won
    assert "does not list the booking" in result.reason


@pytest.mark.parametrize("burst", [(0.0,), (0.0, 0.1, 0.2)])
async def test_exhausted_burst_reports_why(make_backend, make_client, clock, burst):
    backend = make_backend(opens_in=0.1)
    backend.t0 = time.time() + 30  # never opens within the burst
    client = await make_client(backend)

    result = await race(
        client, RacePlan(class_id=CLASS_ID, t0=time.time() + 0.05, burst=burst), clock
    )
    await client.aclose()

    assert not result.registered
    assert f"exhausted {len(burst)}" in result.reason
    assert len(result.attempts) == len(burst)

"""True time, and sleeping until an exact moment.

Why this module exists at all: a Raspberry Pi or a mini-PC routinely runs 200-600ms
away from true time between ntpd corrections, and a class whose registration opens
at 14:10:00 is decided inside the first few hundred milliseconds. A racer that
trusts `time.time()` can therefore be perfectly coded and still lose every time,
silently, with nothing in the logs to explain it.

Two ideas carry the module:

1. Measure the local clock's error against real NTP servers and keep the offset,
   with a dispersion figure that says how much to trust it.
2. Never sleep against the wall clock. Convert the target into a *monotonic*
   deadline once, then wait against that. A wall clock can be stepped backwards
   by ntpd mid-wait; the monotonic clock cannot.
"""

from __future__ import annotations

import logging
import socket
import statistics
import struct
import time
from dataclasses import dataclass

logger = logging.getLogger("phantom_tap.clock")

# NTP counts seconds from 1900; unix time counts from 1970.
_NTP_EPOCH_DELTA = 2_208_988_800

DEFAULT_SERVERS = ("time.google.com", "pool.ntp.org", "time.cloudflare.com")

# Below this many seconds to go, stop sleeping and spin. The OS gives no better
# than ~1-15ms on a sleep wakeup; spinning the last slice costs one core for a
# few hundredths of a second and buys sub-millisecond arrival.
SPIN_WINDOW = 0.05


@dataclass(frozen=True)
class Sample:
    offset: float  # add to local time to get true time
    delay: float  # round trip, the uncertainty of this one sample
    server: str


@dataclass(frozen=True)
class Sync:
    """The result of a sync, and the only thing the rest of the code needs."""

    offset: float
    dispersion: float  # spread across servers; large means do not trust the offset
    samples: int
    synced_at_monotonic: float

    @property
    def trustworthy(self) -> bool:
        """Is this good enough to race on?

        50ms of disagreement between servers is already more than the window we
        are fighting over, so treat it as unusable rather than quietly racing blind.
        """
        return self.samples >= 2 and self.dispersion < 0.05

    def age(self) -> float:
        return time.monotonic() - self.synced_at_monotonic


class UnsyncedClock(RuntimeError):
    """Refused to race: we do not know what time it is well enough."""


def query_ntp(server: str, timeout: float = 2.0) -> Sample:
    """One SNTP round trip.

    Implemented directly rather than via ntplib: it is 20 lines, removes a
    dependency from the one code path that must never fail to import, and lets us
    keep the round-trip delay, which ntplib's simple API discards.
    """
    packet = bytearray(48)
    packet[0] = 0x1B  # leap=0, version=3, mode=3 (client)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        t1 = time.time()
        sock.sendto(bytes(packet), (server, 123))
        data, _ = sock.recvfrom(48)
        t4 = time.time()
    finally:
        sock.close()

    if len(data) < 48:
        raise OSError(f"{server}: short NTP reply ({len(data)} bytes)")

    # t2 = server receive (bytes 32:40), t3 = server transmit (bytes 40:48)
    t2 = _ntp_to_unix(data[32:40])
    t3 = _ntp_to_unix(data[40:48])
    if t3 == 0:
        raise OSError(f"{server}: NTP reply has a zero transmit timestamp")

    return Sample(offset=((t2 - t1) + (t3 - t4)) / 2, delay=(t4 - t1) - (t3 - t2), server=server)


def _ntp_to_unix(raw: bytes) -> float:
    seconds, fraction = struct.unpack("!II", raw)
    return seconds + fraction / 2**32 - _NTP_EPOCH_DELTA


def sync(servers: tuple[str, ...] = DEFAULT_SERVERS, timeout: float = 2.0) -> Sync:
    """Ask several servers and combine them.

    The median, not the mean: one server behind a congested path produces a wild
    offset, and a mean would drag the answer toward it.
    """
    samples: list[Sample] = []
    for server in servers:
        try:
            sample = query_ntp(server, timeout)
        except OSError as exc:
            logger.warning("ntp: %s unreachable (%s)", server, exc)
            continue
        logger.debug("ntp: %s offset=%+.4fs delay=%.4fs", server, sample.offset, sample.delay)
        samples.append(sample)

    if not samples:
        raise UnsyncedClock(f"no NTP server answered (tried: {', '.join(servers)})")

    offsets = [s.offset for s in samples]
    result = Sync(
        offset=statistics.median(offsets),
        dispersion=(max(offsets) - min(offsets)) if len(offsets) > 1 else float(samples[0].delay),
        samples=len(samples),
        synced_at_monotonic=time.monotonic(),
    )
    level = logging.INFO if result.trustworthy else logging.WARNING
    logger.log(
        level,
        "ntp: offset=%+.4fs dispersion=%.4fs from %d/%d servers (local clock is %s)",
        result.offset,
        result.dispersion,
        result.samples,
        len(servers),
        "fast" if result.offset < 0 else "slow",
    )
    return result


class Clock:
    """Corrected time plus precise waiting.

    `sync_result=None` means "trust the local clock", which is what the tests and
    `--dry-run` use. The racing path requires a real sync.
    """

    def __init__(self, sync_result: Sync | None = None) -> None:
        self.sync = sync_result

    @property
    def offset(self) -> float:
        return self.sync.offset if self.sync else 0.0

    def now(self) -> float:
        """Unix time, corrected."""
        return time.time() + self.offset

    def monotonic_deadline(self, target_unix: float) -> float:
        """Pin a wall-clock target to the monotonic timeline, once.

        Everything after this point is immune to ntpd stepping the wall clock.
        """
        return time.monotonic() + (target_unix - self.now())

    def sleep_until(self, target_unix: float, *, spin: float = SPIN_WINDOW) -> float:
        """Block until `target_unix`. Returns the overshoot in seconds.

        Coarse sleep down to `spin` seconds out, then a busy wait. The returned
        overshoot is recorded per attempt: if it ever grows, the machine is too
        loaded to race and that must be visible rather than inferred.
        """
        deadline = self.monotonic_deadline(target_unix)

        coarse = deadline - time.monotonic() - spin
        if coarse > 0:
            time.sleep(coarse)
        while time.monotonic() < deadline:
            pass

        return time.monotonic() - deadline

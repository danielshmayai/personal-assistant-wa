"""Keep the computer awake while a race is in progress - Windows only.

A home Windows PC will go into idle sleep on its own schedule, and if that
happens between the lead-in and T0 the race is simply never run. From the moment
the lead-in starts until the race is decided we tell Windows that the system is
required (SetThreadExecutionState). Outside that window nothing is held, so the
PC keeps its normal power behaviour the rest of the week.

What this cannot do is wake a PC that is *already* asleep when the lead-in
arrives - that needs the computer kept on, or a wake timer. See README, Windows.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Iterator

logger = logging.getLogger("phantom_tap.keepawake")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


@contextlib.contextmanager
def keep_awake() -> Iterator[None]:
    if sys.platform != "win32":
        yield
        return

    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    # Per thread: entered and released on the event-loop thread that runs the race.
    if kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) == 0:
        logger.warning("Windows refused to hold the system awake; idle sleep could interrupt the race")
    else:
        logger.info("holding the system awake until the race is decided")
    try:
        yield
    finally:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)

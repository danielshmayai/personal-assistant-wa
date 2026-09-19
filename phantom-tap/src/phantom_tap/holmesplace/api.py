"""The Holmes Place HTTP client.

One concrete client, driven by the protocol description in `config/endpoints.json`.

Everything here is shaped by a single constraint: **at T0 the client must do no
work it could have done earlier.** Serialising a body, resolving DNS, or opening a
TLS connection at T0 costs more than the margin we are racing for. So the client
separates *preparing* a request from *sending* it, and the race only ever sends.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

import httpx

from phantom_tap import pathspec
from phantom_tap.holmesplace.models import (
    ClassSlot,
    Credentials,
    Endpoint,
    Endpoints,
    ProtocolError,
    Seat,
    render,
    slot_from,
)

logger = logging.getLogger("phantom_tap.api")


class Outcome(str, Enum):
    """What an attempt meant. The race branches on exactly these."""

    OK = "ok"
    ALREADY = "already"  # already registered - a win, not an error
    NOT_OPEN = "not_open"  # fired early, or the server is lagging - retry
    FULL = "full"  # lost the race - stop
    SEAT_TAKEN = "seat_taken"  # someone took that seat - try the next preference
    RETRY = "retry"  # 5xx, timeout, transport blip
    FATAL = "fatal"  # auth is broken or the protocol moved - stop and shout

    @property
    def is_terminal(self) -> bool:
        return self in (Outcome.OK, Outcome.ALREADY, Outcome.FULL, Outcome.FATAL)


@dataclass
class Attempt:
    outcome: Outcome
    status: int | None
    latency: float
    fired_at: float  # offset from T0 in seconds; negative means we fired early
    detail: str = ""
    body: dict[str, Any] | None = None


class HolmesPlaceClient:
    """Async client over the discovered protocol.

    Not a context manager by accident: the connection is opened minutes before T0
    and must stay warm across the wait, so its lifetime is the caller's business.
    """

    def __init__(
        self,
        endpoints: Endpoints,
        credentials: Credentials,
        *,
        timeout: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.ep = endpoints
        self.creds = credentials
        self._token: str | None = None
        self._client = httpx.AsyncClient(
            base_url=endpoints.base_url,
            headers=endpoints.headers,
            timeout=httpx.Timeout(timeout, connect=4.0),
            http2=True,
            # One connection, kept alive across the whole wait. Several would mean
            # several TLS handshakes and no guarantee the fire uses a warm one.
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
            transport=transport,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    # ----------------------------------------------------------------- auth --

    async def login(self) -> None:
        ctx = {
            "username": self.creds.username,
            "password": self.creds.password,
            "club_id": self.creds.club_id,
        }
        ep = self.ep.login
        response = await self._client.request(
            ep.method,
            render(ep.path, ctx),
            params=render(ep.query, ctx) or None,
            json=render(ep.json_body, ctx) if ep.json_body is not None else None,
        )
        if response.status_code not in ep.success_status:
            raise ProtocolError(
                f"login failed: HTTP {response.status_code} {_excerpt(response)}"
            )
        token = pathspec.resolve(_json(response), self.ep.token_path)
        if not token:
            raise ProtocolError(
                f"login succeeded but no token at {self.ep.token_path!r}. "
                f"The protocol changed - re-run `pt capture`."
            )
        self._token = str(token)
        logger.info("logged in as %s (club %s)", self.creds.username, self.creds.club_id)

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            raise ProtocolError("not authenticated - call login() first")
        return {self.ep.auth_header: self.ep.auth_format.format(token=self._token)}

    async def prewarm(self) -> float:
        """Open the TLS connection now so T0 pays only one round trip.

        Returns the latency of the warming request, which doubles as our estimate
        of how long the real fire will take.
        """
        import time

        started = time.monotonic()
        try:
            await self._client.get("/", headers=self._auth_headers() if self._token else None)
        except httpx.HTTPError as exc:
            logger.debug("prewarm probe errored (harmless, the socket is what matters): %s", exc)
        elapsed = time.monotonic() - started
        logger.info("connection warm (%.0fms round trip)", elapsed * 1000)
        return elapsed

    # ------------------------------------------------------------- read-only --

    async def schedule(self, day: date) -> list[ClassSlot]:
        ep = self.ep.schedule
        ctx = self._ctx(date=day.isoformat(), day=day.isoformat())
        response = await self._client.request(
            ep.method,
            render(ep.path, ctx),
            params=render(ep.query, ctx) or None,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return self._slots(_json(response), ep)

    def _slots(self, payload: Any, ep: Endpoint) -> list[ClassSlot]:
        items = pathspec.resolve(payload, ep.items_path) if ep.items_path else payload
        if not isinstance(items, list):
            raise ProtocolError(
                f"expected a list of classes at {ep.items_path!r}, got "
                f"{type(items).__name__}. Re-run `pt analyze`."
            )
        slots, broken = [], 0
        for item in items:
            try:
                slots.append(slot_from(item, ep.fields))
            except ProtocolError:
                broken += 1
        if broken and not slots:
            raise ProtocolError(f"none of the {broken} schedule entries matched the field map")
        if broken:
            logger.warning("skipped %d schedule entries that did not match the field map", broken)
        return slots

    async def find_class(self, day: date, name: str, start_hhmm: str) -> ClassSlot | None:
        """Locate one class by the two things a human actually knows about it."""
        wanted = name.strip().casefold()
        for slot in await self.schedule(day):
            if slot.start.strftime("%H:%M") != start_hhmm:
                continue
            if wanted in slot.name.casefold() or slot.name.casefold() in wanted:
                return slot
        return None

    async def seat_map(self, class_id: str) -> list[Seat]:
        ep = self.ep.seat_map
        if ep is None:
            return []
        ctx = self._ctx(class_id=class_id)
        response = await self._client.request(
            ep.method,
            render(ep.path, ctx),
            params=render(ep.query, ctx) or None,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return self._seats(_json(response), ep)

    def _seats(self, payload: Any, ep: Endpoint) -> list[Seat]:
        items = pathspec.resolve(payload, ep.items_path) if ep.items_path else payload
        if not isinstance(items, list):
            return []
        num_path = ep.fields.get("number", "number")
        avail_path = ep.fields.get("available", "available")
        seats = []
        for item in items:
            if isinstance(item, int):  # some APIs just return the free numbers
                seats.append(Seat(number=item, available=True))
                continue
            number = pathspec.resolve(item, num_path)
            if number is None:
                continue
            seats.append(Seat(number=int(number), available=bool(pathspec.resolve(item, avail_path))))
        return seats

    async def my_bookings(self) -> list[dict[str, Any]]:
        ep = self.ep.my_bookings
        if ep is None:
            return []
        ctx = self._ctx()
        response = await self._client.request(
            ep.method,
            render(ep.path, ctx),
            params=render(ep.query, ctx) or None,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        items = pathspec.resolve(_json(response), ep.items_path) if ep.items_path else []
        return items if isinstance(items, list) else []

    async def verify_booked(self, class_id: str) -> bool:
        """Independent confirmation. The POST's own answer is never trusted.

        Prefers the bookings list; falls back to re-reading the class off the
        schedule, because a booking that only exists in a 200 response is not a
        booking you can turn up to.
        """
        ep = self.ep.my_bookings
        if ep is not None:
            key = ep.fields.get("class_id", "class_id")
            for booking in await self.my_bookings():
                if str(pathspec.resolve(booking, key)) == str(class_id):
                    return True
            return False

        today = datetime.now(tz=_tz()).date()
        for slot in await self.schedule(today):
            if slot.id == str(class_id):
                return slot.booked
        return False

    # ----------------------------------------------------------- the race ops --

    def prepare_register(self, class_id: str) -> httpx.Request:
        """Build the registration request now so T0 only has to send bytes."""
        ep = self.ep.register
        ctx = self._ctx(class_id=class_id)
        return self._client.build_request(
            ep.method,
            render(ep.path, ctx),
            params=render(ep.query, ctx) or None,
            json=render(ep.json_body, ctx) if ep.json_body is not None else None,
            headers=self._auth_headers(),
        )

    def prepare_seat(self, class_id: str, seat: int) -> httpx.Request:
        ep = self.ep.confirm_seat
        if ep is None:
            raise ProtocolError("no confirm_seat endpoint was discovered")
        ctx = self._ctx(class_id=class_id, seat=seat)
        return self._client.build_request(
            ep.method,
            render(ep.path, ctx),
            params=render(ep.query, ctx) or None,
            json=render(ep.json_body, ctx) if ep.json_body is not None else None,
            headers=self._auth_headers(),
        )

    async def send(self, request: httpx.Request, *, expect: Endpoint) -> tuple[Outcome, httpx.Response | None, str]:
        """Send a prepared request and classify the answer.

        Never raises for a protocol-level refusal: at T0 an exception is just a
        slower way to learn something the caller has to branch on anyway.
        """
        try:
            response = await self._client.send(request)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return Outcome.RETRY, None, f"{type(exc).__name__}: {exc}"

        text = response.text or ""
        signal = self.ep.signals.classify(text)
        if signal == "not_open":
            return Outcome.NOT_OPEN, response, _excerpt(response)
        if signal == "full":
            return Outcome.FULL, response, _excerpt(response)
        if signal == "already":
            return Outcome.ALREADY, response, _excerpt(response)
        if signal == "seat_taken":
            return Outcome.SEAT_TAKEN, response, _excerpt(response)

        if response.status_code in expect.success_status:
            return Outcome.OK, response, ""
        if response.status_code in (401, 403):
            return Outcome.FATAL, response, f"auth rejected: {_excerpt(response)}"
        if response.status_code == 409:
            # Ambiguous by design across these APIs: "already booked" and "class
            # full" both land here. Without a configured phrase, stop and verify
            # rather than guess - re-firing could cancel a registration we won.
            return Outcome.ALREADY, response, f"409, verifying: {_excerpt(response)}"
        if response.status_code == 429 or response.status_code >= 500:
            return Outcome.RETRY, response, f"HTTP {response.status_code}"
        return Outcome.FATAL, response, f"HTTP {response.status_code} {_excerpt(response)}"

    # ------------------------------------------------------------- internals --

    def _ctx(self, **extra: Any) -> dict[str, Any]:
        return {
            "club_id": self.creds.club_id,
            "username": self.creds.username,
            "token": self._token or "",
            **extra,
        }


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise ProtocolError(
            f"{response.request.method} {response.request.url.path} returned "
            f"non-JSON ({response.headers.get('content-type', '?')}): {_excerpt(response)}"
        ) from exc


def _excerpt(response: httpx.Response, limit: int = 180) -> str:
    text = (response.text or "").strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


def _tz():  # pragma: no cover - trivial indirection kept for test patching
    from phantom_tap.holmesplace.models import ISRAEL

    return ISRAEL

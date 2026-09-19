from __future__ import annotations

import time

import pytest

from phantom_tap.clock import Clock
from phantom_tap.holmesplace.api import HolmesPlaceClient
from phantom_tap.holmesplace.models import Credentials, Endpoints
from tests.fake_holmesplace import ENDPOINTS, FakeHolmesPlace


@pytest.fixture
def endpoints() -> Endpoints:
    return Endpoints.from_dict(ENDPOINTS)


@pytest.fixture
def creds() -> Credentials:
    return Credentials(username="0501234567", password="hunter2", club_id="modiin")


@pytest.fixture
def clock() -> Clock:
    """No NTP in tests - the local clock is the reference the fake also uses."""
    return Clock(None)


@pytest.fixture
def make_backend():
    """A fake whose T0 is a short, real wait away, so timing is genuinely exercised."""

    def _make(*, opens_in: float = 0.25, **kwargs) -> FakeHolmesPlace:
        return FakeHolmesPlace(t0=time.time() + opens_in, **kwargs)

    return _make


@pytest.fixture
def make_client(endpoints, creds):
    async def _make(backend: FakeHolmesPlace) -> HolmesPlaceClient:
        client = HolmesPlaceClient(endpoints, creds, transport=backend.transport())
        await client.login()
        return client

    return _make

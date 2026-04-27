"""
Live smoke test — hits the running backend at localhost:8000.

Skipped automatically when the backend is not reachable, so it never
blocks commits on a machine where the stack isn't running.

Run manually:
    pytest backend/tests/test_live.py -v
"""

import os
import time

import pytest
import requests

BASE_URL = os.getenv("PA_BASE_URL", "http://localhost:8000")
TOKEN    = os.getenv("TEST_TOKEN", "")
TIMEOUT  = 20  # seconds — generous enough for first Gemini token


def _backend_reachable() -> bool:
    try:
        requests.get(f"{BASE_URL}/health", timeout=3)
        return True
    except Exception:
        return False


skip_if_down = pytest.mark.skipif(
    not _backend_reachable(),
    reason="Backend not reachable — skipping live tests",
)


# ---------------------------------------------------------------------------
# 1. Health check
# ---------------------------------------------------------------------------

@skip_if_down
def test_health_returns_ok():
    """/health must return 200 with ollama and postgres both ok."""
    r = requests.get(f"{BASE_URL}/health", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body.get("checks", {}).get("postgres") == "ok", f"postgres not ok: {body}"
    assert body.get("checks", {}).get("ollama") == "ok", f"ollama not ok: {body}"


# ---------------------------------------------------------------------------
# 2. /test endpoint — Gemini round-trip
# ---------------------------------------------------------------------------

@skip_if_down
@pytest.mark.skipif(not TOKEN, reason="TEST_TOKEN not set")
def test_agent_responds_within_timeout():
    """/test must return a non-empty reply within TIMEOUT seconds."""
    start = time.monotonic()
    r = requests.post(
        f"{BASE_URL}/test",
        json={"text": "Reply with exactly the number 42 and nothing else."},
        headers={"X-Test-Token": TOKEN},
        timeout=TIMEOUT,
    )
    elapsed = time.monotonic() - start

    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "reply" in body, f"No 'reply' key in response: {body}"
    assert body["reply"].strip(), "Reply is empty"
    assert elapsed < TIMEOUT, f"Response took {elapsed:.1f}s, limit is {TIMEOUT}s"


@skip_if_down
@pytest.mark.skipif(not TOKEN, reason="TEST_TOKEN not set")
def test_agent_reply_is_string():
    """/test reply must be a plain string, not a list or dict."""
    r = requests.post(
        f"{BASE_URL}/test",
        json={"text": "Say hello."},
        headers={"X-Test-Token": TOKEN},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200
    assert isinstance(r.json().get("reply"), str)


@skip_if_down
def test_unauthorized_request_rejected():
    """/test must return 401/403 when called without a valid token."""
    r = requests.post(
        f"{BASE_URL}/test",
        json={"text": "hello"},
        headers={"X-Test-Token": "wrong-token"},
        timeout=5,
    )
    assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"

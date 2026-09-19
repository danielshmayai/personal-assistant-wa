"""A LangChain tool that lets danidin drive phantom-tap — without being in the race.

Drop this into the assistant's tool registry (see integration/README.md). The
crucial boundary: the LLM graph must never sit on the T0 code path. Gemini takes
1–5 non-deterministic seconds and the graph's recursion can stretch further, which
would lose the race outright. So this tool only ever *schedules* a booking and
*reads back* results. The racing is done by the phantom-tap daemon, on time, every
time, with no model in the loop.

Wiring, matching backend/app/graph/tools_registry.py:

    from integration.danidin_tool import get_phantom_tools
    if on("phantom"):
        tools += get_phantom_tools(chat_id)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from langchain_core.tools import tool

PT = ["pt"]  # the phantom-tap CLI on PATH, or an absolute path to .venv/bin/pt
CWD = Path("/home/pi/phantom-tap")  # where booking.toml lives on the mini-PC


def _run(args: list[str], timeout: float = 30.0) -> str:
    try:
        out = subprocess.run(
            PT + args, cwd=CWD, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return f"phantom-tap unavailable: {exc}"
    return (out.stdout + out.stderr).strip()


def get_phantom_tools(chat_id: str) -> list:
    """Tools for danidin to see and steer the class-booking watcher."""

    @tool
    def list_class_watches() -> str:
        """Show the Holmes Place classes phantom-tap is set to book, and when each
        registration window next opens. Read-only."""
        return _run(["plan"]) or "no watches configured"

    @tool
    def show_booking_history(count: int = 5) -> str:
        """Show the results of the last few booking attempts: booked or not, which
        seat, and how the clock and timing behaved. Read-only."""
        return _run(["history", "-n", str(max(1, min(count, 20)))])

    @tool
    def check_booking_readiness() -> str:
        """Check that phantom-tap is ready to book: config, discovered protocol,
        stored credentials and clock sync. Read-only; run this if a booking was
        missed to find out why."""
        return _run(["doctor"])

    # Deliberately no "book now" tool. Booking is time-critical and belongs to the
    # daemon; exposing a model-triggered live booking would put an unpredictable
    # LLM latency on the one path that must be exact. If a class needs adding, that
    # is a config edit a human makes, not a sentence the model acts on at T0.
    return [list_class_watches, show_booking_history, check_booking_readiness]

"""In-process counters for the Assistant-health panel.

Per process and since its start (the WhatsApp engine and the product each
keep their own); durable numbers — memory writes, tool calls — come from the
database instead. Kept per tenant scope so one tenant's panel never shows
another's traffic.
"""
from __future__ import annotations

import statistics
from collections import defaultdict, deque

_MAX_RUNS = 200

_latencies: dict[str, deque] = defaultdict(lambda: deque(maxlen=_MAX_RUNS))
_retrieval: dict[str, dict] = defaultdict(lambda: {"turns": 0, "with_memory": 0, "with_notes": 0})


def _scope() -> str:
    from app.context import current_tenant_id
    return current_tenant_id.get()


def record_reply(duration_ms: int) -> None:
    _latencies[_scope()].append(duration_ms)


def record_retrieval(injected: bool, notes: bool) -> None:
    r = _retrieval[_scope()]
    r["turns"] += 1
    r["with_memory"] += int(injected)
    r["with_notes"] += int(notes)


def snapshot() -> dict:
    lat = list(_latencies[_scope()])
    r = dict(_retrieval[_scope()])
    return {
        "replies": len(lat),
        "median_reply_ms": round(statistics.median(lat)) if lat else None,
        "p90_reply_ms": round(statistics.quantiles(lat, n=10)[-1]) if len(lat) >= 10 else None,
        "retrieval": r,
        "notes_hit_rate": round(r["with_notes"] / r["turns"], 2) if r["turns"] else None,
    }

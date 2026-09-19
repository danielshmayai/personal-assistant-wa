"""Turn a capture into `config/endpoints.json`.

Inference, not magic - every rule here is a stated assumption, and every result
is printed back with the evidence that produced it so a human can reject it.
Three of the rules do most of the work:

*The token is found by correlation.* The capture replaces secrets with stable
hash markers, so the field in the login response whose marker later shows up in
an Authorization header is the token, whatever the backend chose to call it.

*The registration-open field is found by arithmetic.* Every class the app showed
opened exactly five hours before it started. When a date field sits five hours
before the start field, that is the registration-open field even if its name is
`rgDt`. A name-based guess cannot do that.

*The class id is found by reuse.* The POST that registers carries an id that also
appears in the schedule listing. That is what makes it the register endpoint,
rather than the fact that its path contains the word "register".
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from phantom_tap import pathspec
from phantom_tap.holmesplace.models import EXPECTED_LEAD, parse_dt

MARKER = re.compile(r"«SECRET:([0-9a-f]{8}):(\d+)»")

_ID_KEY = re.compile(r"^(id|_id|uid|guid)$|(class|lesson|event|session|activity)_?id$", re.I)
_NAME_KEY = re.compile(r"(^|_)(name|title|caption)$|class_?name|lesson_?name", re.I)
_START_KEY = re.compile(r"start|from|begin|^date$|date_?time", re.I)
_END_KEY = re.compile(r"end|until|finish|^to$", re.I)
_OPEN_KEY = re.compile(r"open|registration|signup|sign_?up|booking_?start|reg_", re.I)
_CAP_KEY = re.compile(r"capacity|max|limit|total|places|slots|size", re.I)
_REG_KEY = re.compile(r"regist|booked|taken|occupied|current|participants|count|attend", re.I)
_SEAT_KEY = re.compile(r"seat|spot|place|position|mat|bike", re.I)
_MINE_KEY = re.compile(r"^(is|has|my|am)_?(regist|book|sign|join)|registered$|booked$", re.I)
_INSTRUCTOR_KEY = re.compile(r"instructor|trainer|teacher|coach", re.I)
_STUDIO_KEY = re.compile(r"studio|room|hall|location|area", re.I)


@dataclass
class Flow:
    ts: float
    method: str
    scheme: str
    host: str
    path: str
    query: dict[str, Any]
    req_headers: dict[str, str]
    req_body: Any
    status: int
    resp_body: Any

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}"

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def __str__(self) -> str:
        return f"{self.method} {self.path} -> {self.status}"


@dataclass
class Detection:
    endpoints: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.notes.append(text)


def load(path: str | Path) -> list[Flow]:
    flows = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_no} is not valid JSON: {exc}") from None
        flows.append(
            Flow(
                ts=raw.get("ts", 0.0),
                method=raw.get("method", "GET"),
                scheme=raw.get("scheme", "https"),
                host=raw.get("host", ""),
                path=raw.get("path", "/"),
                query=raw.get("query") or {},
                req_headers=raw.get("req_headers") or {},
                req_body=raw.get("req_body"),
                status=raw.get("status", 0),
                resp_body=raw.get("resp_body"),
            )
        )
    return flows


# --------------------------------------------------------------------------- #


def analyze(flows: list[Flow]) -> Detection:
    det = Detection()
    if not flows:
        det.missing.append("the capture is empty - no Holmes Place traffic was recorded")
        return det

    det.endpoints["base_url"] = _base_url(flows, det)
    det.endpoints["headers"] = _common_headers(flows)

    login = _find_login(flows, det)
    if login:
        det.endpoints["login"], det.endpoints["auth"] = login
    else:
        det.missing.append("login (no request carrying a password was seen)")

    schedule, items = _find_schedule(flows, det)
    if schedule:
        det.endpoints["schedule"] = schedule
    else:
        det.missing.append("schedule (no response contained a list of dated classes)")

    class_ids = _ids_from(items, det.endpoints.get("schedule", {}).get("fields", {}))

    register = _find_register(flows, class_ids, det)
    if register:
        det.endpoints["register"] = register
    else:
        det.missing.append("register (no POST reused an id from the schedule)")

    seat_map, seat_numbers = _find_seat_map(flows, det)
    if seat_map:
        det.endpoints["seat_map"] = seat_map
        confirm = _find_confirm_seat(flows, seat_numbers, class_ids, det)
        if confirm:
            det.endpoints["confirm_seat"] = confirm
        else:
            det.note("no seat-confirm request seen - book a seated class during capture")

    det.endpoints["signals"] = _find_signals(flows, det)
    return det


def _base_url(flows: list[Flow], det: Detection) -> str:
    counts = Counter(f.origin for f in flows)
    # Prefer a host that looks like an API over a CDN or the marketing site.
    api = [o for o in counts if re.search(r"(^|\.)api[.-]|/api", o)]
    chosen = max(api, key=lambda o: counts[o]) if api else counts.most_common(1)[0][0]
    det.note(f"base_url {chosen} ({counts[chosen]} of {len(flows)} exchanges)")
    return chosen


def _common_headers(flows: list[Flow]) -> dict[str, str]:
    """Headers sent on nearly every request - the app's fingerprint.

    Some backends reject a request that does not look like the app, so these are
    replayed verbatim. Authorization is excluded: it is built per request.
    """
    total = len(flows)
    tally: Counter[tuple[str, str]] = Counter()
    for flow in flows:
        for key, value in flow.req_headers.items():
            if key in ("authorization", "content-type") or MARKER.search(str(value)):
                continue
            tally[(key, str(value))] += 1
    return {k: v for (k, v), n in tally.items() if n >= max(2, total * 0.6)}


def _find_login(flows: list[Flow], det: Detection) -> tuple[dict, dict] | None:
    """The request that sends a password and gets back a string used as a bearer."""
    used_markers = {
        m.group(1)
        for f in flows
        for m in [MARKER.search(str(f.req_headers.get("authorization", "")))]
        if m
    }
    for flow in flows:
        if flow.method == "GET" or not flow.ok or not isinstance(flow.req_body, dict):
            continue
        pw_paths = pathspec.find_paths(
            flow.req_body, lambda k, v: bool(k) and bool(re.search(r"pass|pwd", k, re.I))
        )
        if not pw_paths:
            continue
        token_paths = pathspec.find_paths(
            flow.resp_body,
            lambda k, v: isinstance(v, str)
            and (m := MARKER.search(v)) is not None
            and m.group(1) in used_markers,
        )
        if not token_paths:
            det.note(f"{flow} sends a password but its response never became a bearer token")
            continue
        token_path = min(token_paths, key=len)
        auth_raw = next(
            (str(f.req_headers.get("authorization", "")) for f in flows if f.req_headers.get("authorization")),
            "Bearer x",
        )
        auth_format = "Bearer {token}" if auth_raw.lower().startswith("bearer") else "{token}"
        det.note(f"login: {flow}; token at {token_path} (proved by the Authorization header)")
        return (
            {
                "method": flow.method,
                "path": flow.path,
                "json": _templatize_body(flow.req_body),
                "token_path": token_path,
                "success_status": [flow.status],
            },
            {"header": "Authorization", "format": auth_format},
        )
    return None


def _templatize_body(body: Any) -> Any:
    """Replace the captured credentials with the placeholders the client fills in."""
    if not isinstance(body, dict):
        return body
    out = {}
    for key, value in body.items():
        if re.search(r"pass|pwd", key, re.I):
            out[key] = "{password}"
        elif re.search(r"user|email|phone|mobile|login|member", key, re.I):
            out[key] = "{username}"
        elif re.search(r"club|branch|site|location", key, re.I):
            out[key] = "{club_id}"
        else:
            out[key] = redact_markers(value)
    return out


def redact_markers(value: Any) -> Any:
    if isinstance(value, str) and MARKER.search(value):
        return ""
    if isinstance(value, dict):
        return {k: redact_markers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_markers(v) for v in value]
    return value


def _find_schedule(flows: list[Flow], det: Detection) -> tuple[dict | None, list[dict]]:
    best: tuple[int, Flow, str, list[dict]] | None = None
    for flow in flows:
        if not flow.ok or flow.resp_body is None:
            continue
        for path, items in _lists_of_objects(flow.resp_body):
            dated = [len(_datetimes(i)) for i in items[:5]]
            # A class carries at least two timestamps (it starts and it ends), and
            # usually three. That pair is the gate; without it a long list of
            # anything else - seats, clubs, banners - can win on length alone.
            if not any(n >= 2 for n in dated):
                continue
            score = 10 * sum(1 for n in dated if n >= 2) + sum(dated) + min(len(items), 20)
            if best is None or score > best[0]:
                best = (score, flow, path, items)
    if best is None:
        return None, []

    _, flow, items_path, items = best
    fields = _map_fields(items, det)
    det.note(f"schedule: {flow}; {len(items)} entries at {items_path or '<root>'}")
    return (
        {
            "method": flow.method,
            "path": flow.path,
            "query": _templatize_query(flow.query),
            "items_path": items_path,
            "fields": fields,
        },
        items,
    )


def _lists_of_objects(node: Any, prefix: str = "") -> list[tuple[str, list[dict]]]:
    out = []
    if isinstance(node, list) and node and all(isinstance(i, dict) for i in node):
        out.append((prefix, node))
    elif isinstance(node, dict):
        for key, value in node.items():
            out.extend(_lists_of_objects(value, f"{prefix}.{key}" if prefix else key))
    return out


# Below this, a number is a count, not an epoch. `parse_dt` will happily read
# maxCapacity=45 as 1970-01-01T00:00:45, which silently turned the capacity field
# into a date candidate and let a 48-entry seat map outscore the real schedule.
_PLAUSIBLE_EPOCH = 1_000_000_000  # 2001-09-09


def _datetimes(item: dict) -> dict[str, Any]:
    out = {}
    for key, value in item.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float) and value < _PLAUSIBLE_EPOCH:
            continue
        parsed = parse_dt(value) if isinstance(value, str | int | float) else None
        if parsed:
            out[key] = parsed
    return out


def _map_fields(items: list[dict], det: Detection) -> dict[str, str]:
    sample = max(items[:10], key=len, default={})
    fields: dict[str, str] = {}

    def pick(target: str, pattern: re.Pattern[str], want: Any = None) -> None:
        for key, value in sample.items():
            if not pattern.search(key):
                continue
            # bool is a subclass of int, so an int field must exclude it explicitly
            # or "hasSpots": true would be read as the capacity.
            if want is bool and not isinstance(value, bool):
                continue
            if want is int and (isinstance(value, bool) or not isinstance(value, int)):
                continue
            if want is str and not isinstance(value, str):
                continue
            fields[target] = key
            return

    pick("id", _ID_KEY)
    pick("name", _NAME_KEY, str)
    pick("instructor", _INSTRUCTOR_KEY, str)
    pick("studio", _STUDIO_KEY, str)
    pick("capacity", _CAP_KEY, int)
    pick("registered", _REG_KEY, int)
    pick("has_seats", _SEAT_KEY, bool)
    pick("booked", _MINE_KEY, bool)

    dates = _datetimes(sample)
    starts = [k for k in dates if _START_KEY.search(k)] or sorted(dates, key=lambda k: dates[k])
    if starts:
        fields["start"] = starts[0]
    ends = [k for k in dates if _END_KEY.search(k) and k != fields.get("start")]
    if ends:
        fields["end"] = ends[0]

    opens = _find_opens_at(sample, dates, fields.get("start"), det)
    if opens:
        fields["opens_at"] = opens
    else:
        det.note(
            "WARNING: no registration-open field found. Without it the racer has no "
            "T0. Capture a class that is not open yet (the app shows 'פתיחת הרשמה')."
        )
    return fields


def _find_opens_at(
    sample: dict, dates: dict[str, Any], start_key: str | None, det: Detection
) -> str | None:
    """Arithmetic first, naming second.

    A field sitting exactly `EXPECTED_LEAD` before the start is the registration-open
    field regardless of its name. Only if no field does that do we fall back to
    guessing from key names, and say so.
    """
    if start_key and start_key in dates:
        start = dates[start_key]
        for key, value in dates.items():
            if key == start_key:
                continue
            if abs((start - value) - EXPECTED_LEAD) < timedelta(minutes=2):
                det.note(f"opens_at: {key!r} sits exactly 5h before {start_key!r} - the observed rule")
                return key
    named = [k for k in dates if _OPEN_KEY.search(k) and k != start_key]
    if named:
        det.note(f"opens_at: {named[0]!r} guessed from its name - verify this before racing")
        return named[0]
    return None


def _templatize_query(query: dict[str, Any]) -> dict[str, str]:
    out = {}
    for key, value in query.items():
        text = str(value)
        if re.search(r"club|branch|site", key, re.I):
            out[key] = "{club_id}"
        elif re.search(r"date|day|from|start", key, re.I) and parse_dt(text):
            out[key] = "{date}"
        else:
            out[key] = text
    return out


def _ids_from(items: list[dict], fields: dict[str, str]) -> set[str]:
    key = fields.get("id")
    if not key:
        return set()
    return {str(i[key]) for i in items if key in i}


def _ok_first(flows: list[Flow]) -> list[Flow]:
    """Successful exchanges first.

    A capture usually contains the tap that was too early as well as the one that
    worked. Reading the failed one as the template is silently fatal: its status
    becomes `success_status`, and the racer then treats its own 200 as an error.
    """
    return [f for f in flows if f.ok] + [f for f in flows if not f.ok]


def _find_register(flows: list[Flow], class_ids: set[str], det: Detection) -> dict | None:
    for flow in _ok_first(flows):
        if flow.method not in ("POST", "PUT", "PATCH") or not class_ids:
            continue
        hit = _which_id(flow, class_ids)
        if not hit:
            continue
        if re.search(r"seat|spot|place", flow.path, re.I):
            continue  # that is the seat confirm, a different step
        if not flow.ok:
            det.note(
                f"WARNING: register was only seen failing ({flow}). success_status is "
                f"a guess - capture a booking that actually succeeds."
            )
        det.note(f"register: {flow}; reuses schedule id {hit}")
        return {
            "method": flow.method,
            "path": flow.path.replace(hit, "{class_id}"),
            "json": _sub_id(flow.req_body, hit, "{class_id}"),
            "success_status": [flow.status] if flow.ok else [200, 201],
        }
    return None


def _which_id(flow: Flow, ids: set[str]) -> str | None:
    blob = flow.path + json.dumps(flow.req_body, ensure_ascii=False) + json.dumps(flow.query)
    # Longest first, so id "7" never shadows id "771".
    for candidate in sorted(ids, key=len, reverse=True):
        if re.search(rf"(?<![0-9A-Za-z]){re.escape(candidate)}(?![0-9A-Za-z])", blob):
            return candidate
    return None


def _sub_id(body: Any, value: str, placeholder: str) -> Any:
    if isinstance(body, dict):
        return {k: _sub_id(v, value, placeholder) for k, v in body.items()}
    if isinstance(body, list):
        return [_sub_id(v, value, placeholder) for v in body]
    if str(body) == value:
        return placeholder
    return redact_markers(body)


def _find_seat_map(flows: list[Flow], det: Detection) -> tuple[dict | None, set[int]]:
    for flow in flows:
        if not flow.ok or not re.search(r"seat|spot|place|map", flow.path, re.I):
            continue
        for path, items in _lists_of_objects(flow.resp_body):
            numbers = {
                int(v)
                for i in items
                for k, v in i.items()
                if _SEAT_KEY.search(k) and isinstance(v, int) and not isinstance(v, bool)
            }
            if not numbers:
                continue
            sample = items[0]
            number_key = next((k for k in sample if _SEAT_KEY.search(k) and isinstance(sample[k], int)), "number")
            avail_key = next(
                (k for k in sample if isinstance(sample[k], bool) and re.search(r"avail|free|open|empty", k, re.I)),
                "available",
            )
            det.note(f"seat_map: {flow}; {len(numbers)} seats at {path or '<root>'}")
            return (
                {
                    "method": flow.method,
                    "path": re.sub(r"/\d+", "/{class_id}", flow.path, count=1),
                    "items_path": path,
                    "fields": {"number": number_key, "available": avail_key},
                },
                numbers,
            )
    return None, set()


def _find_confirm_seat(
    flows: list[Flow], seats: set[int], class_ids: set[str], det: Detection
) -> dict | None:
    for flow in _ok_first(flows):
        if flow.method not in ("POST", "PUT", "PATCH"):
            continue
        # A bare number match is not enough: "/api/v2/classes/77104/register" contains
        # a standalone "2" (from "v2") and would otherwise read as "claim seat 2".
        # Require the request to be about seats before believing the number.
        body_text = json.dumps(flow.req_body, ensure_ascii=False)
        seat_shaped = bool(_SEAT_KEY.search(flow.path)) or any(
            _SEAT_KEY.search(k) for k in (flow.req_body or {}) if isinstance(flow.req_body, dict)
        )
        if not seat_shaped:
            continue
        blob = flow.path + body_text
        # Longest first: "/api/v2/" offers a standalone "2", and seat 12 must win
        # over it rather than losing to set iteration order.
        seat_hit = next(
            (s for s in sorted(seats, key=lambda n: (-len(str(n)), -n))
             if re.search(rf"(?<!\d){s}(?!\d)", blob)),
            None,
        )
        if seat_hit is None:
            continue
        path = flow.path
        class_hit = _which_id(flow, class_ids)
        if class_hit:
            path = path.replace(class_hit, "{class_id}")
        path = re.sub(rf"(?<!\d){seat_hit}(?!\d)", "{seat}", path)
        det.note(f"confirm_seat: {flow}; seat {seat_hit}")
        return {
            "method": flow.method,
            "path": path,
            "json": _sub_id(_sub_id(flow.req_body, str(class_hit or ""), "{class_id}"), str(seat_hit), "{seat}"),
            "success_status": [flow.status],
        }
    return None


def _find_signals(flows: list[Flow], det: Detection) -> dict[str, list[str]]:
    """Collect the refusal phrases the server actually used.

    Only errors seen during capture end up here. The racer treats an unknown
    refusal conservatively, so an incomplete list costs a retry, never a booking.
    """
    signals: dict[str, list[str]] = {"not_open": [], "full": [], "already": [], "seat_taken": []}
    patterns = {
        "not_open": r"not.{0,10}open|טרם נפתח|לא נפתח|too early|not.{0,10}start",
        "full": r"\bfull\b|no.{0,10}(space|places|spots)|מלא|אין מקום|תפוס",
        "already": r"already.{0,15}(regist|book)|כבר רשום|כבר נרשמת",
        "seat_taken": r"seat.{0,15}(taken|occupied|unavail)|spot.{0,15}taken|המקום תפוס",
    }
    for flow in flows:
        if flow.ok or not flow.resp_body:
            continue
        text = json.dumps(flow.resp_body, ensure_ascii=False)
        for label, pattern in patterns.items():
            match = re.search(pattern, text, re.I)
            if match and match.group(0) not in signals[label]:
                signals[label].append(match.group(0))
                det.note(f"signal {label}: {match.group(0)!r} (from {flow})")
    return {k: v for k, v in signals.items() if v}

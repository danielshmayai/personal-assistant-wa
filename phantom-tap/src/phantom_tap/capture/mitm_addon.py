"""mitmproxy addon: record the app's real conversation with its backend.

Run it as:

    mitmdump -s src/phantom_tap/capture/mitm_addon.py

then drive the app by hand - log in, open the schedule, register for a class,
pick a seat. Every exchange with the Holmes Place backend lands in
`captures/<timestamp>.jsonl`.

**Secrets never reach the file.** A password or a token is replaced by a marker
`«SECRET:ab12cd34:214»` - a short hash of the value and its length. That is not
cosmetic: the hash is *stable*, so `pt analyze` can still prove that the string
in the login response is the same string that later appears in the Authorization
header, which is exactly how it finds the token field - while the file itself
stays safe to read, copy and paste into a bug report.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

HOST_PATTERN = re.compile(os.getenv("PT_CAPTURE_HOSTS", r"holmesplace|holmes-place"), re.I)

# Keys whose values are secret regardless of what they look like.
SECRET_KEYS = re.compile(
    r"pass(word|wd)?|secret|token|auth|credential|otp|pin|jwt|bearer|session[_-]?id|refresh",
    re.I,
)
# Values that look like credentials even under an innocent key name.
SECRETISH = re.compile(r"^(ey[A-Za-z0-9_-]{10,}\.|[A-Za-z0-9_\-+/=]{32,}$)")

HEADER_ALLOWLIST = {
    "content-type", "accept", "accept-language", "user-agent", "authorization",
    "x-api-key", "x-app-version", "x-client-version", "x-device-id", "x-platform",
    "origin", "referer",
}


def mark(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:8]
    return f"«SECRET:{digest}:{len(value)}»"


def redact(node: Any, key: str | None = None) -> Any:
    if isinstance(node, dict):
        return {k: redact(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [redact(v, key) for v in node]
    if isinstance(node, str):
        # Checked first: an Authorization value matches SECRET_KEYS on its key, and
        # blanking it whole would destroy the one thing analyze needs from it - the
        # scheme. Keep "Bearer", mark only the credential.
        if node.lower().startswith("bearer ") and len(node) > 20:
            return "Bearer " + mark(node[7:])
        if (key and SECRET_KEYS.search(key)) or SECRETISH.match(node):
            return mark(node)
    return node


def _body(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return redact(json.loads(raw))
    except (ValueError, UnicodeDecodeError):
        return {"__non_json__": len(raw)}


def _headers(items) -> dict[str, str]:  # type: ignore[no-untyped-def]
    out = {}
    for name, value in items:
        low = name.lower()
        if low in HEADER_ALLOWLIST:
            out[low] = redact(value, low)
    return out


class HolmesCapture:
    def __init__(self) -> None:
        self.path: Path | None = None
        self.fh = None
        self.count = 0

    def _open(self):  # type: ignore[no-untyped-def]
        """Create the capture file on the first matching exchange, not at import.

        mitmproxy wants `addons` at module scope, so the addon is constructed just
        by importing this module - including when the tests import `redact`. Doing
        filesystem work there would litter a capture file on every import.
        """
        if self.fh is not None:
            return self.fh
        out_dir = Path(os.getenv("PT_CAPTURE_DIR", "captures"))
        out_dir.mkdir(parents=True, exist_ok=True)
        self.path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        # 0600: even redacted, this file maps your gym account's whole API surface.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self.fh = os.fdopen(fd, "a", encoding="utf-8")
        print(f"[phantom-tap] capturing hosts matching /{HOST_PATTERN.pattern}/ -> {self.path}")
        return self.fh

    def response(self, flow) -> None:  # type: ignore[no-untyped-def] - mitmproxy hook
        host = flow.request.pretty_host
        if not HOST_PATTERN.search(host):
            return
        record = {
            "ts": time.time(),
            "method": flow.request.method,
            "scheme": flow.request.scheme,
            "host": host,
            "path": flow.request.path.split("?")[0],
            "query": dict(flow.request.query),
            "req_headers": _headers(flow.request.headers.items()),
            "req_body": _body(flow.request.raw_content or b""),
            "status": flow.response.status_code,
            "resp_headers": _headers(flow.response.headers.items()),
            "resp_body": _body(flow.response.raw_content or b""),
        }
        fh = self._open()
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()
        self.count += 1
        print(f"[phantom-tap] {self.count:3d}  {record['method']:6s} {record['status']}  {record['path']}")

    def done(self) -> None:
        if self.fh is None:
            print("[phantom-tap] no Holmes Place traffic was seen - is the proxy set on the device?")
            return
        self.fh.close()
        print(f"[phantom-tap] wrote {self.count} exchanges to {self.path}")
        print("[phantom-tap] next: pt analyze " + str(self.path))


addons = [HolmesCapture()]

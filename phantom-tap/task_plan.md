# phantom-tap — Technical Plan

## Goal (Milestone 1, concrete, no generalisation)
Book a specific Holmes Place studio class the instant its registration window
opens, unattended, from a Mini-PC at home driving a real Android device.

## Ground truth observed from the live app (screenshots, 16/09)

Facts the design is built on, not assumptions:

- Club is selectable per account ("הולמס פלייס מודיעין" dropdown) -> every request
  is scoped by a club id. Multi-club accounts exist; the club must be pinned.
- Schedule tab ("לו\"ז שיעורים") is a per-day list. A class card carries:
  date, start-end time, class name, instructor, studio, `registered/capacity`
  (e.g. 43/45) and the action.
- **The server publishes the registration-open moment.** Cards not yet open show
  `פתיחת הרשמה: 16/09 14:10` and offer `תזכורת` instead of `הרשמה`.
  Observed: BODY POWER 19:10 -> opens 14:10 same day. HIIT 18:30 -> opens 13:30.
  Both are exactly **start - 5h**.
  => T0 is *read from the API*, never typed by the user. The 5h rule is only a
     sanity assertion that fires an alert if the server ever disagrees.
- **Booking is two-phase.** `הרשמה` leads to `בחירת מקום` - a numbered floor map
  (1..48 for that studio) and a `המשך` confirm. Winning the registration does not
  mean winning the seat you want.

## Why the design looks like this

**The problem is not tapping. It is latency and clock truth.**
A class whose registration opens at 20:00:00 is won in the first ~300ms.

| Path | Time from T0 to request landing | Verdict |
|---|---|---|
| UI automation (uiautomator2 taps through the app) | 3–15s | loses the race |
| Replayed HTTP call to `api.holmesplace.co.il` | 50–200ms | wins |

So: **HTTP-first, UI-fallback.** The Android device is a *protocol laboratory*
and a *token refresher*, not the thing that races. The UI path exists only so
the system still books (late) when the protocol breaks.

**Second-order problem: the Pi's clock.** `ntpd` drift of 400ms is common and
silently turns a winning strategy into a losing one. Hence a dedicated NTP
offset estimator rather than trusting `time.time()`.

## Repo layout

```
phantom-tap/
├── pyproject.toml            # uv, py3.12, ruff+mypy strict
├── task_plan.md
├── README.md                 # runbook for the Mini-PC
├── config/
│   ├── endpoints.example.json   # written for real by `pt analyze`
│   └── booking.example.toml     # which class, when it opens
├── src/phantom_tap/
│   ├── clock.py              # NTP offset (multi-sample median), monotonic T0 alignment
│   ├── secrets.py            # Fernet-at-rest, key from 0600 file; never logged
│   ├── store.py              # sqlite3, explicit schema+migrations, all writes in txn
│   ├── notify.py             # Telegram / WAHA webhook
│   ├── capture/
│   │   ├── mitm_addon.py     # filters *.holmesplace.co.il -> captures/*.jsonl (redacted)
│   │   ├── pinning.py        # detects TLS pinning; emits the Frida plan if pinned
│   │   ├── unpin.js          # Frida script, universal pinning bypass
│   │   └── analyze.py        # capture -> config/endpoints.json (login/schedule/book)
│   ├── holmesplace/
│   │   ├── api.py            # ONE concrete client: login, clubs, schedule,
│   │   │                     #   register, seat_map, confirm_seat, my_bookings
│   │   ├── models.py         # ClassSlot(opens_at, registered, capacity, has_seats)
│   │   └── ui.py             # uiautomator2 fallback: schedule -> הרשמה ->
│   │                         #   בחירת מקום -> המשך, screenshot per step
│   ├── race.py               # the core: prewarm TLS+token, fire at T0, burst retry
│   ├── orchestrator.py       # T0-300s warm -> T0 fire -> verify -> notify -> record
│   └── cli.py                # pt capture | analyze | book | daemon | verify
├── scripts/
│   ├── device_setup.sh       # adb reverse-proxy, CA install, pinning check
│   └── install_systemd.sh
└── tests/
    ├── fake_holmesplace.py   # ASGI fake: "not open yet" -> opens at T0 -> full -> race
    ├── test_clock.py         # offset math, drift, T0 alignment under fake time
    ├── test_race.py          # fires within budget; retry/backoff; idempotency
    ├── test_api.py           # respx: login, token refresh, 409 handling, verify
    └── test_analyze.py       # capture fixture -> expected endpoints.json
```

## The race algorithm (`race.py`) — the part that matters

```
discovery  poll schedule for the target class -> read `opens_at` from the server.
           assert opens_at == start - 5h; if not, alert and trust the server.
T0-300s    login, store token; open TLS connection; HTTP/2 ping keepalive.
           fetch the studio's seat map (static layout) and cache it.
T0-60s     re-validate token (refresh if TTL <10min); resolve exact class id;
           pre-serialise BOTH request bodies (register, and seat-confirm with a
           placeholder seat) so T0 does zero JSON work.
T0-2s      sleep until T0-50ms on the NTP-corrected monotonic clock, then spin.

PHASE 1 - claim the registration
T0+0       send pre-built register request on the warm connection
T0+..      burst retry at +0, +120ms, +350ms, +800ms, +1.5s, +3s
           stop on: registered | definitive "class full" | verified booking
           retryable: 5xx, timeout, "registration not yet open"

PHASE 2 - claim the seat (only if the class has a seat map)
           response of phase 1 carries the available-seat set; pick the first
           entry of `preferred_spots` that is free, else the configured fallback
           (nearest-to-preferred by floor-map distance, or any free seat).
           confirm immediately - the hold window between phases is short and
           unknown, so phase 2 is fired on the same warm connection with no
           user interaction and its own short retry burst.

verify     independent GET of the schedule/my-bookings; the booking AND the seat
           must appear. Never trust the POST response alone.
```
Idempotency: every attempt carries a client-side attempt id; before each retry
we check whether a prior attempt already succeeded, so we never double-book.
Partial-failure rule: registered-but-no-seat is a *success with a warning*, not a
retry - re-firing phase 1 risks cancelling the registration we just won.

## Capture flow (runs once, on your Mini-PC, human-assisted)
1. `scripts/device_setup.sh` — verifies adb, installs the mitmproxy CA, routes
   the device through the Mini-PC, then reports pinned/not-pinned.
2. `pt capture` — you open the app and book one class by hand. Traffic lands in
   `captures/*.jsonl` with credentials redacted at write time.
3. `pt analyze` — infers login endpoint + token JSONPath + schedule endpoint +
   book endpoint/params, writes `config/endpoints.json`, prints what it inferred
   so you can eyeball it.
4. `pt book --dry-run` — replays against the real API in read-only mode.

## Non-negotiables
- No secret ever hits a log line, a commit, or a capture file.
- Nothing runs against the live API without an explicit `--live`; default dry-run.
- Every attempt is recorded in sqlite; a missed booking must be explainable.
- `.gitignore` covers `captures/`, `config/endpoints.json`, `config/booking.toml`, `*.key`.

## Known constraints, stated up front
- Replaying a private mobile API for your own account is very likely against
  Holmes Place's ToS, and the endpoints can change without notice. The system is
  built to *detect* breakage (schema assertions + verify step + alert) rather
  than pretend it won't happen.
- If the device is not rooted, installing a system-trusted CA is not possible on
  Android 7+. Then `pt capture` cannot see the traffic and Milestone 1 degrades
  to the UI path until a rooted/Magisk device or an emulator is available.

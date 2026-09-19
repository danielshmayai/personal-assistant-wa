# phantom-tap

Book a Holmes Place studio class the instant its registration opens.

The app publishes, per class, the exact moment registration opens
("פתיחת הרשמה: 16/09 14:10"). The good classes fill in the first few hundred
milliseconds after that. phantom-tap watches for that moment, then races.

## How it works, and why

**HTTP-first, UI-fallback.** Tapping through the app takes 3–15 seconds and loses
any contested class. Replaying the app's own call to `api.holmesplace.co.il` lands
in 50–200ms and wins. So the Android device is used *once*, to learn the protocol,
and the race itself is a plain HTTP request fired from a mini-PC. The UI path
(`uiautomator2`) stays as a fallback for when the protocol breaks — a late booking
beats no booking.

**The protocol is discovered, not hard-coded.** A private API you don't control
will change field names and paths without notice. So instead of hand-writing them,
`pt capture` records the app's real traffic and `pt analyze` infers the endpoints,
proving the token field by correlation and finding the registration-open field by
arithmetic (the timestamp exactly five hours before the class start). When the
backend changes, you re-capture rather than re-code.

**Time is treated as a hard problem.** A Raspberry Pi's clock drifts hundreds of
milliseconds between corrections — enough to lose on its own. The racer measures
its offset against several NTP servers, takes the median, and waits against a
monotonic deadline so a correction mid-wait can't move the target. Arrival is
sub-millisecond.

**A win is never assumed.** Every booking is confirmed by an independent read, not
by trusting the POST's own 200. Being registered without your preferred seat counts
as a win and is never retried — re-firing could cancel the registration you hold.

## The two-phase booking

Registration and seat selection are separate steps (the app shows a floor map —
"בחירת מקום" — after "הרשמה"). phantom-tap claims the registration first, then
claims the best available seat from your ordered `preferred_spots`, falling back to
the nearest free seat. Winning registration but losing the seat is still a booking.

## Setup, in order

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'                 # add ',device' on the mini-PC for the UI path
cp config/booking.example.toml config/booking.toml   # then edit it

# One-time protocol capture, on the machine with the Android device attached:
bash scripts/device_check.sh            # can this device be intercepted at all?
bash scripts/device_setup.sh            # route it through mitmproxy, install the CA
pt capture                              # drive the app by hand; it records the traffic
pt analyze captures/<newest>.jsonl      # -> config/endpoints.json  (read it!)

# Then:
pt login                                # store the account password, encrypted
pt doctor                               # is everything ready?
pt book --watch 1                       # a rehearsal — sends nothing
pt book --watch 1 --live                # the real thing, once
pt daemon                               # every enabled watch, week after week
```

Install `scripts/phantom-tap.service` to run the daemon under systemd.

## Commands

| command | what it does |
|---|---|
| `pt doctor` | checks config, protocol, credentials and clock |
| `pt capture` | prints the mitmproxy command to record the app |
| `pt analyze <file>` | infers `config/endpoints.json` from a capture |
| `pt schedule` | lists a day's classes through the discovered protocol |
| `pt plan` | shows the next window for every watch |
| `pt book` | chases one class (rehearsal by default; `--live` to mean it) |
| `pt daemon` | runs every enabled watch continuously |
| `pt history` | past races, attempt by attempt, with clock and timing |

## Scope and honesty

- This automates **your own** account through the app's own backend. That is
  very likely against Holmes Place's terms of service, and the endpoints can change
  without notice. The system is built to *detect* breakage (schema assertions +
  an independent verify + an alert) rather than pretend it won't happen.
- On Android 7+ a non-rooted phone cannot capture a well-behaved app's traffic.
  `scripts/device_check.sh` tells you where you stand; the realistic capture rig is
  a rooted spare device or an emulator. If the app pins certificates, use a standard
  research tool such as `objection` on a device you own — this repo ships no bypass.

See `task_plan.md` for the full design.

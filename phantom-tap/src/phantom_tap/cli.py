"""`pt` - the command line.

The commands follow the order you actually use them in:

    pt doctor                 is this machine ready to race?
    pt login                  store the account password, encrypted
    pt capture                record the app talking to its backend
    pt analyze <capture>      turn that recording into config/endpoints.json
    pt schedule               read the club's timetable through the inferred protocol
    pt plan                   what would run, and when, for every watch
    pt book --live            chase one class through one window
    pt daemon                 chase every enabled watch, week after week
    pt history                what happened, and why
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from phantom_tap import __version__
from phantom_tap.config import Config, ConfigError
from phantom_tap.holmesplace.models import ISRAEL, Endpoints

DEFAULT_CONFIG = Path("config/booking.toml")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return int(args.func(args) or 0)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pt", description=__doc__.split("\n")[0])
    parser.add_argument("--version", action="version", version=f"phantom-tap {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="check that this machine can race").set_defaults(func=cmd_doctor)
    sub.add_parser("login", help="store the account password, encrypted").set_defaults(func=cmd_login)
    sub.add_parser("capture", help="print the mitmproxy command to record the app").set_defaults(
        func=cmd_capture
    )

    p = sub.add_parser("analyze", help="infer the protocol from a capture")
    p.add_argument("capture", type=Path)
    p.add_argument("-o", "--out", type=Path, default=Path("config/endpoints.json"))
    p.add_argument("--force", action="store_true", help="overwrite an existing endpoints.json")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("schedule", help="list the club's classes for a day")
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p.set_defaults(func=cmd_schedule)

    sub.add_parser("plan", help="show the next window for every watch").set_defaults(func=cmd_plan)

    p = sub.add_parser("book", help="chase one class through one registration window")
    p.add_argument("--watch", type=int, default=1, help="1-based index into [[watch]]")
    p.add_argument("--date", help="YYYY-MM-DD (default: the watch's next occurrence)")
    p.add_argument(
        "--live",
        action="store_true",
        help="actually register. Without it nothing is sent - the default is a rehearsal.",
    )
    p.set_defaults(func=cmd_book)

    sub.add_parser("daemon", help="run every enabled watch").set_defaults(func=cmd_daemon)

    p = sub.add_parser("history", help="past races and their attempts")
    p.add_argument("-n", type=int, default=10)
    p.set_defaults(func=cmd_history)
    return parser


def _setup_logging(verbosity: int) -> None:
    level = logging.DEBUG if verbosity >= 2 else logging.INFO if verbosity else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
    )
    # At T0 the interesting lines are all from the race.
    logging.getLogger("phantom_tap").setLevel(min(level, logging.INFO))
    logging.getLogger("httpx").setLevel(logging.WARNING)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def cmd_doctor(args) -> int:  # type: ignore[no-untyped-def]
    from phantom_tap import clock as clockmod
    from phantom_tap.secrets import SecretStore

    ok = True

    def check(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"  {'✓' if good else '✗'} {label}{': ' + detail if detail else ''}")

    print("configuration")
    try:
        config = Config.load(args.config)
        check(str(args.config), True, f"{len(config.enabled_watches())} enabled watch(es)")
    except (ConfigError, FileNotFoundError) as exc:
        check(str(args.config), False, str(exc))
        return 1

    print("protocol")
    try:
        endpoints = Endpoints.load(config.endpoints_path)
        check(str(config.endpoints_path), True, endpoints.base_url)
        check("seat selection", endpoints.confirm_seat is not None,
              "discovered" if endpoints.confirm_seat else "NOT discovered - seats cannot be claimed")
        check("independent verification", endpoints.my_bookings is not None,
              "via my_bookings" if endpoints.my_bookings else "falls back to re-reading the schedule")
        for name in ("not_open", "full", "already"):
            phrases = getattr(endpoints.signals, name)
            check(f"signal {name}", bool(phrases),
                  ", ".join(phrases) if phrases else "unknown - the racer will be conservative")
    except (FileNotFoundError, KeyError) as exc:
        check(str(config.endpoints_path), False, str(exc))

    print("credentials")
    store = SecretStore(config.key_path, config.secrets_path)
    try:
        check("password", bool(store.get("hp_password", env_fallback="HP_PASSWORD")), "stored")
    except Exception as exc:
        check("password", False, str(exc))

    print("clock")
    try:
        synced = clockmod.sync()
        check(
            "ntp",
            synced.trustworthy,
            f"offset {synced.offset * 1000:+.0f}ms, servers disagree by "
            f"{synced.dispersion * 1000:.0f}ms across {synced.samples}",
        )
    except Exception as exc:
        check("ntp", False, str(exc))

    print("\nready to race" if ok else "\nNOT ready - fix the ✗ lines above")
    return 0 if ok else 1


def cmd_login(args) -> int:  # type: ignore[no-untyped-def]
    from phantom_tap.secrets import SecretStore

    config = Config.load(args.config)
    store = SecretStore(config.key_path, config.secrets_path)
    password = getpass.getpass(f"Holmes Place password for {config.username}: ")
    if not password:
        print("nothing entered", file=sys.stderr)
        return 1
    store.set("hp_password", password)
    print(f"stored, encrypted, in {config.secrets_path} (key: {config.key_path}, mode 600)")
    return 0


def cmd_capture(args) -> int:  # type: ignore[no-untyped-def]
    addon = Path(__file__).parent / "capture" / "mitm_addon.py"
    print(
        f"""Run this, then drive the app by hand.

  mitmdump -s {addon} --listen-port 8080

On the device, point Wi-Fi at this machine's IP on port 8080 and install the
mitmproxy CA. `scripts/device_setup.sh` does both over adb, and
`scripts/device_check.sh` tells you first whether this device can be intercepted
at all.

In the app, do all four of these - each one teaches the analyzer something it
cannot infer from the others:

  1. log in                       -> the login endpoint and the token field
  2. open the schedule            -> the class list and its field names
  3. tap a class that is NOT open -> the refusal phrase, and the opening time
  4. register for one, pick a seat -> the register and seat endpoints

Then: pt analyze captures/<newest>.jsonl"""
    )
    return 0


def cmd_analyze(args) -> int:  # type: ignore[no-untyped-def]
    from phantom_tap.capture.analyze import analyze, load

    detection = analyze(load(args.capture))

    print(f"read {args.capture}\n")
    for note in detection.notes:
        print(f"  {'!' if note.startswith('WARNING') else '·'} {note}")

    if detection.missing:
        print("\ncould not find:")
        for item in detection.missing:
            print(f"  ✗ {item}")
        print("\nCapture again, covering the steps `pt capture` lists.")
        if "register" in " ".join(detection.missing) or "login" in " ".join(detection.missing):
            return 1

    if args.out.exists() and not args.force:
        print(f"\n{args.out} already exists. Pass --force to overwrite it.")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(detection.endpoints, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {args.out}")
    print("Read it before racing - every line above is an inference, not a fact.")
    print("Then: pt doctor && pt book --watch 1   (a rehearsal; add --live to mean it)")
    return 0


def cmd_schedule(args) -> int:  # type: ignore[no-untyped-def]
    from phantom_tap.orchestrator import make_client

    config = Config.load(args.config)
    endpoints = Endpoints.load(config.endpoints_path)
    day = date.fromisoformat(args.date) if args.date else datetime.now(tz=ISRAEL).date()

    async def run() -> int:
        client = make_client(config, endpoints)
        try:
            await client.login()
            slots = await client.schedule(day)
        finally:
            await client.aclose()

        print(f"{day.isoformat()} — {len(slots)} classes\n")
        for slot in sorted(slots, key=lambda s: s.start):
            opens = (
                slot.opens_at.astimezone(ISRAEL).strftime("%d/%m %H:%M")
                if slot.opens_at
                else "—"
            )
            flag = "" if slot.lead_matches_expectation or not slot.opens_at else " (not -5h!)"
            print(
                f"  {slot.start.astimezone(ISRAEL):%H:%M}  {slot.name[:28]:<28} "
                f"{str(slot.registered) + '/' + str(slot.capacity) if slot.capacity else '':>7}  "
                f"opens {opens}{flag}{'  [booked]' if slot.booked else ''}"
            )
        return 0

    return asyncio.run(run())


def cmd_plan(args) -> int:  # type: ignore[no-untyped-def]
    from phantom_tap.orchestrator import NotYetPublished, make_client, plan_for, resolve
    from phantom_tap.race import burst_for  # noqa: F401 - documents the link

    config = Config.load(args.config)
    endpoints = Endpoints.load(config.endpoints_path)

    async def run() -> int:
        client = make_client(config, endpoints)
        try:
            await client.login()
            for index, watch in enumerate(config.watches, start=1):
                day = watch.next_date()
                mark = " " if watch.enabled else "-"
                try:
                    slot = await resolve(client, watch, day)
                    plan = plan_for(watch, slot)
                    opens = datetime.fromtimestamp(plan.t0, tz=ISRAEL)
                    wait = opens - datetime.now(tz=ISRAEL)
                    print(
                        f"{mark}{index}. {slot.describe()}\n"
                        f"     opens {opens:%a %d/%m %H:%M} (in {_human(wait)}), "
                        f"{len(plan.burst)} attempts over {plan.burst[-1]:.0f}s, "
                        f"seats {watch.preferred_spots or '(any)'}"
                    )
                except NotYetPublished as exc:
                    print(f"{mark}{index}. {watch.label()}\n     {exc}")
        finally:
            await client.aclose()
        return 0

    return asyncio.run(run())


def cmd_book(args) -> int:  # type: ignore[no-untyped-def]
    from phantom_tap.notify import build as build_notifier
    from phantom_tap.orchestrator import book
    from phantom_tap.store import Store

    config = Config.load(args.config)
    endpoints = Endpoints.load(config.endpoints_path)
    try:
        watch = config.watches[args.watch - 1]
    except IndexError:
        print(f"no [[watch]] #{args.watch}; there are {len(config.watches)}", file=sys.stderr)
        return 1

    if not args.live:
        print("REHEARSAL — nothing will be sent. Add --live to actually register.\n")

    store = Store(config.db_path)
    try:
        result = asyncio.run(
            book(
                config,
                endpoints,
                watch,
                store=store,
                notifier=build_notifier(config.notify),
                live=args.live,
                day=date.fromisoformat(args.date) if args.date else None,
            )
        )
    finally:
        store.close()

    print(f"\n{result.summary()}")
    return 0 if (result.won or not args.live) else 1


def cmd_daemon(args) -> int:  # type: ignore[no-untyped-def]
    from phantom_tap.orchestrator import daemon

    config = Config.load(args.config)
    endpoints = Endpoints.load(config.endpoints_path)
    asyncio.run(daemon(config, endpoints))
    return 0


def cmd_history(args) -> int:  # type: ignore[no-untyped-def]
    from phantom_tap.store import Store

    config = Config.load(args.config)
    store = Store(config.db_path)
    try:
        races = store.history(args.n)
        if not races:
            print("nothing recorded yet")
            return 0
        for race in races:
            status = "booked" if race["verified"] else ("UNCONFIRMED" if race["registered"] else "lost")
            seat = f" seat {race['seat']}" if race["seat"] is not None else ""
            print(
                f"\n{race['created_at']}  {race['class_label'] or race['class_id']}\n"
                f"  {status}{seat}  ·  clock {race['clock_offset'] * 1000:+.0f}ms  ·  "
                f"overshoot {race['max_overshoot_ms']:.1f}ms"
                + (f"\n  {race['reason']}" if race["reason"] else "")
            )
            for attempt in store.attempts_for(race["id"]):
                print(
                    f"    {attempt['seq']:>3}. T0{attempt['fired_at'] * 1000:+8.0f}ms  "
                    f"{attempt['outcome']:<10} {attempt['status'] or '---'}  "
                    f"{attempt['latency'] * 1000:5.0f}ms  {attempt['detail'][:60]}"
                )
    finally:
        store.close()
    return 0


def _human(delta: timedelta) -> str:
    seconds = delta.total_seconds()
    if seconds < 0:
        return "already open"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

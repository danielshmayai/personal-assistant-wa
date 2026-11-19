# phantom-tap for Android

Books a Holmes Place class **from the phone itself**, by operating the Holmes
Place app the way you would: open it, find the class, wait while the button says
"תזכורת", tap "הרשמה" the moment it appears, pick the first red (free) seat in
your order, tap "המשך".

## Why this, and not the PC version

| | Phone app (this) | PC version (`../src`) |
|---|---|---|
| Needs the app's private protocol recorded | **no** | yes |
| Needs a computer on, or on the same network | **no** | yes |
| Needs root | **no** | for recording, usually |
| Stores your Holmes Place password | **no** (uses the app you are logged into) | yes, encrypted |
| Speed at the opening moment | tens of ms after the button flips | 50-200 ms |
| Needs the phone unlocked at the window | **yes** | no |

The last row is the real cost. Android does not let any app - rightly - operate
another app behind a secure lock screen. Two minutes before each window the
phone lights up and asks you to unlock it (fingerprint is enough). If it stays
locked, that booking is missed, and the result notification says so.

## Install

1. Copy `app-debug.apk` to the phone and open it. Allow "install unknown apps"
   for whatever opened it.
2. **Android 13+:** Settings → Apps → phantom-tap → ⋮ (top corner) →
   **Allow restricted settings**. Without this, step 4 is greyed out for any
   app not installed from a store.
3. Open phantom-tap. Fix every ❌ row with its button.
4. Settings → Accessibility → Installed apps → phantom-tap → **On**.
   The service is scoped to `com.holmesplace` only (see
   `app/src/main/res/xml/accessibility_service_config.xml`): it cannot see or
   touch any other app.
5. Add a class: name exactly as the app shows it, weekday, start time, seats in
   order of preference.
6. Press **בדיקה** (rehearsal). It opens Holmes Place and drives all the way to
   the class card **without registering**, then reports what it found. Do this
   once per class before relying on it.

On Samsung, also: Settings → Battery → Background usage limits → **Never
sleeping apps** → add phantom-tap.

## How the timing works

- An `AlarmManager.setAlarmClock` alarm fires two minutes before each window.
  Alarm clocks are the one alarm type Android delivers on time through Doze and
  Samsung's battery management.
- The app arrives at the class card early and **watches** it. You reported that
  "הרשמה" appears by itself at the published minute, so the first two seconds past
  it are spent watching only; if it has not flipped by then, the list is refreshed
  (from its top - pull-to-refresh only works there) every 1.5 s, for up to 90 s.
- "תזכורת" is never tapped: it sets a reminder, it does not register.
- The opening time is the class start minus 5 hours - true for every class seen.
  It is adjustable per class, and early-morning classes are worth checking:
  a 07:15 class would open at 02:15.

## Build

```bash
./gradlew :app:assembleDebug        # -> app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:testDebugUnitTest    # 40 tests
```

Needs JDK 17+ and the Android SDK (`sdk.dir` in `local.properties`, or
`ANDROID_HOME`). Android Studio does both.

## Layout

```
core/   pure Kotlin, no Android - all the decisions, all unit-tested
  AppDriver   the booking flow, over an abstract Screen
  Schedule    which window is next, and when to wake
  SeatPlan    seat order when the preferred ones are gone
  TrueClock   SNTP offset, to know the true minute
app/    the Android shell around it
  HolmesA11yService + A11yScreen   reads the Holmes Place screen, taps it
  BookingScheduler / Receivers     the alarms, re-armed after reboot
  BookingService                   one run: unlock, keep screen on, drive, report
  MainActivity                     setup, status, classes, history
```

The core is tested against `FakeHolmesApp`, a model of the screens in the
user's screenshots (RTL day strip, cards, red/gray seats, a disabled "המשך").

## Known unknowns, to settle on the phone with **בדיקה**

- Whether the app exposes its labels to accessibility as separate elements. If
  a card is one merged element, the rehearsal will report the card not found.
- Whether a registration is kept if the seat is never confirmed. Until known,
  that case is reported as "check the app", never as a booking.

# phantom-tap for Android

Books your **❤️ favourite** Holmes Place classes **from the phone itself**, by
operating the Holmes Place app the way you would: open "מועדפים", find the class,
wait while the button says "תזכורת", tap "הרשמה" the moment it appears, pick the
first red (free) seat in your order, tap "המשך".

**The favourites are the only source.** Nothing is typed into phantom-tap: heart
a class in the Holmes Place app and phantom-tap picks it up, reads its
"פתיחת הרשמה" time off the card, and arms an alarm. Un-heart it and the booking
is dropped. The full timetable ("לו״ז שיעורים") is never used.

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
5. Set your seat order once, for all classes ("עריכת מקומות").
6. Heart classes in the Holmes Place app, then press **סנכרון מועדפים**. It opens
   Holmes Place, reads the favourites tab and lists what it found.
7. Press **בדיקה** (rehearsal) on a class. It drives all the way to its card
   **without registering**, then reports what it found.

## When the favourites are read

| When | What it may do |
|---|---|
| "סנכרון מועדפים" button | add, update, and remove un-hearted classes |
| right after every booking (phone already unlocked) | the same |
| whenever you look at the favourites tab yourself | add and update only - never remove, since what is off screen is unknown, not gone |

An empty read is never trusted to mean "nothing is a favourite any more": a
list that failed to load looks exactly the same. "לא להירשם" on a class keeps it
from being booked without it reappearing at the next sync.

On Samsung, also: Settings → Battery → Background usage limits → **Never
sleeping apps** → add phantom-tap.

## Stopping it

| How | Reach | Effect |
|---|---|---|
| "עצור הכל" on any phantom-tap notification | the notification shade | stops the running session within one poll (80 ms) and switches the automation off |
| Quick Settings tile "phantom-tap" | the shade, even while the screen is being driven | the same; tap again to switch back on |
| the switch at the top of the app | the app | the same |
| Settings → Accessibility → phantom-tap → off | system settings | nothing can tap at all |

Off means off: the running session ends, every alarm is cancelled, nothing is
read in the background, and nothing restarts until it is switched back on.
A stopped class is never retried by itself - "נסה שוב" in the app does that.

It also stops **itself**, and says why, when something looks like a bug:

- more than 6 sessions in an hour - a healthy week is a handful;
- a single session running longer than 6 minutes.

Every booking attempt is marked as attempted *before* it starts. Without that, a
run that failed before starting left its class pending, and the class was
re-armed one second out, over and over, until its window closed.

## How the timing works

- An `AlarmManager.setAlarmClock` alarm fires two minutes before each window.
  Alarm clocks are the one alarm type Android delivers on time through Doze and
  Samsung's battery management.
- The app arrives at the class card early and **watches** it. You reported that
  "הרשמה" appears by itself at the published minute, so the first two seconds past
  it are spent watching only; if it has not flipped by then, the list is refreshed
  (from its top - pull-to-refresh only works there) every 1.5 s, for up to 90 s.
- "תזכורת" is never tapped: it sets a reminder, it does not register.
- The opening time is read off the card ("פתיחת הרשמה: 25/11 14:10"). Only if a
  card does not show it is the class start minus 5 hours used, and the app marks
  that time as estimated.

## Build

```bash
./gradlew :app:assembleDebug        # -> app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:testDebugUnitTest    # 57 tests
```

Needs JDK 17+ and the Android SDK (`sdk.dir` in `local.properties`, or
`ANDROID_HOME`). Android Studio does both.

## Layout

```
core/   pure Kotlin, no Android - all the decisions, all unit-tested
  AppDriver   the booking flow and the favourites read, over an abstract Screen
  Favorites   reading cards off the screen; reconciling them with the bookings
  Schedule    timing constants
  Safety      stopping a session mid-run; the runaway guard
  SeatPlan    seat order when the preferred ones are gone
  TrueClock   SNTP offset, to know the true minute
app/    the Android shell around it
  HolmesA11yService + A11yScreen   reads the Holmes Place screen, taps it
  BookingScheduler / Receivers     the alarms, re-armed after reboot
  BookingService                   one run: unlock, keep screen on, drive, report
  MainActivity                     setup, status, classes, history
```

The core is tested against `FakeHolmesApp`, a model of the screens in the
user's screenshots (sub-tabs, favourites cards across dates, red/gray seats, a
disabled "המשך", and a registered card whose button the driver does not know).

## Known unknowns, to settle on the phone with **בדיקה**

- Whether the app exposes its labels to accessibility as separate elements. If
  a card is one merged element, the sync will find no classes.
- Whether the app marks the selected sub-tab as selected. If not, the passive
  read never triggers; the button and the after-booking sync still work.
- What a registered favourite's button says. Anything other than the three known
  actions is treated as "already handled" and not booked.
- Whether a registration is kept if the seat is never confirmed. Until known,
  that case is reported as "check the app", never as a booking.

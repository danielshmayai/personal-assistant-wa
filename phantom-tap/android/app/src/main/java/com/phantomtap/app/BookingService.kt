package com.phantomtap.app

import android.app.KeyguardManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import com.phantomtap.core.AppDriver
import com.phantomtap.core.BookingStatus
import com.phantomtap.core.DrivePlan
import com.phantomtap.core.DriveResult
import com.phantomtap.core.Outcome
import com.phantomtap.core.RunawayGuard
import com.phantomtap.core.StoppableTime
import com.phantomtap.core.Stopped
import com.phantomtap.core.TrueClock
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger

/**
 * Every session in which phantom-tap drives the Holmes Place app, in the foreground.
 *
 * Two kinds:
 *  - race: an alarm fired two minutes before a favourite's window. Unlock, keep
 *    the screen on, book it - then, with the phone already unlocked and the app
 *    already open, read the favourites again to pick up new ones.
 *  - sync: the button. Read the favourites and reconcile.
 *
 * Sessions are queued on one thread: there is one screen and one finger. Two
 * classes opening in the same minute are booked one after the other.
 *
 * Every session can be stopped from outside within one poll (see [stopAll]),
 * runs at most [RunawayGuard.MAX_SESSION_MS], and must be admitted by
 * [Automation.admitSession] - which refuses when switched off and switches
 * everything off when sessions come too thick to be anything but a bug.
 */
class BookingService : Service() {
    private val runs = Executors.newSingleThreadExecutor { Thread(it, "phantom-tap-run") }
    private val pending = AtomicInteger(0)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Notifier.ensureChannels(this)
        val key = intent?.getStringExtra(BookingScheduler.EXTRA_KEY)
        val booking = key?.let { BookingStore(this).get(it) }
        val syncOnly = intent?.getBooleanExtra(EXTRA_SYNC, false) == true
        // Foreground first, unconditionally: Android requires it within seconds of the start.
        val notification = Notifier.service(this, booking?.label ?: "סנכרון מועדפים")
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(Notifier.ID_SERVICE, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(Notifier.ID_SERVICE, notification)
        }
        if (booking == null && !syncOnly) {
            stopIfIdle()
            return START_NOT_STICKY
        }
        val rehearsal = intent?.getBooleanExtra(EXTRA_REHEARSAL, false) == true
        pending.incrementAndGet()
        runs.execute {
            // Asked here, on the session thread, so queued sessions are re-checked
            // when their turn comes - a stop pressed meanwhile still holds.
            if (!Automation.admitSession(this)) {
                pending.decrementAndGet()
                stopIfIdle()
                return@execute
            }
            stopReason = null
            busy = true
            try {
                if (syncOnly) sync() else race(booking!!.key, rehearsal)
            } finally {
                busy = false
                pending.decrementAndGet()
                stopIfIdle()
            }
        }
        return START_NOT_STICKY
    }

    private fun stopIfIdle() {
        if (pending.get() == 0) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    override fun onDestroy() {
        runs.shutdownNow()
        super.onDestroy()
    }

    // ------------------------------------------------------------------ race --

    private fun race(key: String, rehearsal: Boolean) {
        val store = BookingStore(this)
        val booking = store.get(key) ?: return
        if (booking.status != BookingStatus.PENDING) return
        if (!rehearsal) {
            // Marked attempted before anything can go wrong. A booking still PENDING
            // after a failed start was re-armed one second out, again and again,
            // until its window closed - a runaway. Whatever happens now, it is
            // attempted once; the user can retry it from the app.
            store.edit { list ->
                list.map { if (it.key == key) it.copy(status = BookingStatus.MISSED, detail = "הריצה לא הסתיימה") else it } to Unit
            }
        }
        withDriver(booking.label, deadlineMs = booking.opensAt.toInstant().toEpochMilli() + 60_000) { driver ->
            val result = driver.run(DrivePlan.from(booking, SeatPrefsStore(this).get()), rehearsal)
            if (!rehearsal) {
                val status = when (result.outcome) {
                    Outcome.BOOKED -> BookingStatus.BOOKED
                    Outcome.WAITLISTED -> BookingStatus.WAITLISTED
                    else -> BookingStatus.MISSED
                }
                val detail = listOfNotNull(result.seat?.let { "מקום $it" }, result.detail.ifEmpty { null }).joinToString(" · ")
                store.edit { list ->
                    list.map { if (it.key == key) it.copy(status = status, detail = detail) else it } to Unit
                }
            }
            record(booking.label, result)
            Notifier.result(this, booking.label, result)

            // The phone is unlocked and the app is open: the cheapest moment there
            // will be to pick up classes hearted since the last read.
            // Not after a stop: "stop" means stop, not "stop, then read the favourites".
            if (!rehearsal && result.outcome != Outcome.STOPPED) syncWith(driver, announce = false)
        }
    }

    // ------------------------------------------------------------------ sync --

    private fun sync() {
        withDriver("סנכרון מועדפים", deadlineMs = System.currentTimeMillis() + 60_000) { driver ->
            syncWith(driver, announce = true)
        }
        // Back to phantom-tap, so the user sees what was found.
        runCatching {
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP))
        }
    }

    private fun syncWith(driver: AppDriver, announce: Boolean) {
        val read = driver.readFavorites()
        val outcome = FavoritesSync.apply(this, read.cards, read.complete)
        val summary = buildString {
            append("נקראו ${read.cards.size} שיעורים מועדפים")
            if (outcome.added.isNotEmpty()) append(" · נוספו ${outcome.added.size}")
            if (outcome.removed.isNotEmpty()) append(" · הוסרו ${outcome.removed.size} שהורד מהם הלב")
            if (!read.complete) append(" · קריאה חלקית (${read.detail}), לא הוסר דבר")
        }
        History(this).add(
            History.Entry(System.currentTimeMillis(), "סנכרון מועדפים", "SYNC", null, summary, read.steps),
        )
        if (announce && outcome.added.isEmpty()) Notifier.problem(this, "סנכרון מועדפים", summary)
    }

    // -------------------------------------------------------------- plumbing --

    /**
     * The shared shell of every session: clock, accessibility, unlock, screen on,
     * wake lock - and their release, whatever happens inside.
     */
    private fun withDriver(label: String, deadlineMs: Long, body: (AppDriver) -> Unit) {
        val wake = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "phantomtap:run")
            .apply { acquire(WAKE_LIMIT_MS) }
        val startedAt = System.currentTimeMillis()
        var overran = false
        var a11y: HolmesA11yService? = null
        try {
            val trueClock = TrueClock().also { if (!it.sync()) Log.w(TAG, "NTP unreachable; using the phone's clock") }
            // Every wait in the session goes through this: a stop, or a session that
            // has run too long, ends it at the next poll wherever it is.
            val clock = StoppableTime(trueClock) {
                stopReason ?: if (System.currentTimeMillis() - startedAt > RunawayGuard.MAX_SESSION_MS) {
                    overran = true
                    "הריצה נמשכה יותר מ-${RunawayGuard.MAX_SESSION_MS / 60_000} דקות"
                } else {
                    null
                }
            }
            a11y = HolmesA11yService.instance ?: return fail(
                label,
                "שירות הנגישות של phantom-tap כבוי - הפעל אותו בהגדרות",
            )
            if (!ensureUnlocked(label, clock, deadlineMs)) {
                return fail(label, "הטלפון נשאר נעול - בלי פתיחת נעילה אי אפשר ללחוץ באפליקציה")
            }
            a11y.keepScreenOn(true)
            body(AppDriver(A11yScreen(a11y), clock) { Log.i(TAG, it) })
        } catch (e: Stopped) {
            val result = DriveResult(Outcome.STOPPED, detail = e.message ?: "נעצר")
            record(label, result)
        } catch (e: Exception) {
            Log.e(TAG, "session failed", e)
            fail(label, "${e.javaClass.simpleName}: ${e.message}")
        } finally {
            a11y?.keepScreenOn(false)
            Notifier.cancelUnlock(this)
            if (wake.isHeld) wake.release()
            BookingScheduler.rescheduleAll(this)
        }
        // A session that ran past its limit is a bug, not bad luck: stop everything.
        if (overran) Automation.disable(this, "הושבת אוטומטית: ריצה נמשכה יותר מ-${RunawayGuard.MAX_SESSION_MS / 60_000} דקות")
    }

    /**
     * An accessibility service cannot act behind a secure lock screen, so wake the
     * screen and ask for an unlock, then wait for it - a late booking beats none.
     */
    private fun ensureUnlocked(label: String, clock: StoppableTime, deadlineMs: Long): Boolean {
        val keyguard = getSystemService(KeyguardManager::class.java)
        if (!keyguard.isKeyguardLocked) return true

        Notifier.unlockPrompt(this, label)
        // Allowed from the background because the accessibility service is bound.
        runCatching { startActivity(Intent(this, UnlockActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        while (keyguard.isKeyguardLocked && clock.now() < deadlineMs) clock.sleep(250)
        Notifier.cancelUnlock(this)
        return !keyguard.isKeyguardLocked
    }

    private fun fail(label: String, detail: String) {
        val result = DriveResult(Outcome.FAILED, detail = detail)
        record(label, result)
        Notifier.result(this, label, result)
    }

    private fun record(label: String, result: DriveResult) {
        History(this).add(
            History.Entry(
                atMs = System.currentTimeMillis(),
                label = label,
                outcome = result.outcome.name,
                seat = result.seat,
                detail = result.detail,
                steps = result.steps,
            ),
        )
    }

    companion object {
        private const val TAG = "phantom-tap"
        private const val EXTRA_REHEARSAL = "rehearsal"
        private const val EXTRA_SYNC = "sync"
        // Two minutes early + 90s of waiting + seats + a sync, with margin.
        private const val WAKE_LIMIT_MS = 10 * 60 * 1000L

        /** True while a session drives the app; passive reads stand aside. */
        @Volatile
        var busy = false
            private set

        /** Set to stop the running session; read at every poll through [StoppableTime]. */
        @Volatile
        private var stopReason: String? = null

        /** Stop the running session now. New ones are kept out by [Automation]. */
        fun stopAll(reason: String) {
            stopReason = reason
        }

        fun race(context: Context, key: String, rehearsal: Boolean) {
            context.startForegroundService(
                Intent(context, BookingService::class.java)
                    .putExtra(BookingScheduler.EXTRA_KEY, key)
                    .putExtra(EXTRA_REHEARSAL, rehearsal),
            )
        }

        fun sync(context: Context) {
            context.startForegroundService(Intent(context, BookingService::class.java).putExtra(EXTRA_SYNC, true))
        }
    }
}

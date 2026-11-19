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
import com.phantomtap.core.DrivePlan
import com.phantomtap.core.DriveResult
import com.phantomtap.core.Outcome
import com.phantomtap.core.Race
import com.phantomtap.core.Schedule
import com.phantomtap.core.TrueClock
import java.time.ZonedDateTime
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger

/**
 * One booking run, in the foreground, from the alarm to the result.
 *
 *   alarm (T0 - 2 min) -> sync the clock -> make sure the phone is unlocked
 *   -> keep the screen on -> drive the Holmes Place app -> record and notify
 *   -> arm next week's alarm
 *
 * Runs are queued on one thread. Two classes opening in the same minute are
 * booked one after the other - the second late - because there is one screen
 * and one finger.
 */
class BookingService : Service() {
    private val runs = Executors.newSingleThreadExecutor { Thread(it, "phantom-tap-run") }
    private val pending = AtomicInteger(0)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Notifier.ensureChannels(this)
        val id = intent?.getStringExtra(BookingScheduler.EXTRA_WATCH)
        val watch = id?.let { WatchStore(this).get(it) }
        val label = watch?.let { "${it.className} ${it.startLabel}" } ?: "שיעור"
        // Foreground first, unconditionally: Android requires it within seconds of the start.
        val notification = Notifier.service(this, label)
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(Notifier.ID_SERVICE, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(Notifier.ID_SERVICE, notification)
        }
        if (watch == null) {
            stopIfIdle()
            return START_NOT_STICKY
        }
        val rehearsal = intent.getBooleanExtra(EXTRA_REHEARSAL, false)
        pending.incrementAndGet()
        runs.execute {
            try {
                run(watch.id, rehearsal)
            } finally {
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

    private fun run(watchId: String, rehearsal: Boolean) {
        val watch = WatchStore(this).get(watchId) ?: return
        val race = Schedule.nextRace(watch, ZonedDateTime.now(Schedule.ISRAEL))
        val label = "${watch.className} ${watch.startLabel} (${race.dayLabel})"
        val wake = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "phantomtap:run")
            .apply { acquire(WAKE_LIMIT_MS) }
        var a11y: HolmesA11yService? = null
        try {
            val clock = TrueClock().also { if (!it.sync()) Log.w(TAG, "NTP unreachable; using the phone's clock") }

            a11y = HolmesA11yService.instance ?: return finish(
                label,
                DriveResult(Outcome.FAILED, detail = "שירות הנגישות של phantom-tap כבוי - הפעל אותו בהגדרות"),
            )
            if (!ensureUnlocked(label, race, clock, rehearsal)) {
                return finish(label, DriveResult(Outcome.FAILED, detail = "הטלפון נשאר נעול - בלי פתיחת נעילה אי אפשר ללחוץ באפליקציה"))
            }
            a11y.keepScreenOn(true)
            val result = AppDriver(A11yScreen(a11y), clock) { Log.i(TAG, it) }.run(DrivePlan.from(race), rehearsal)
            finish(label, result)
        } catch (e: Exception) {
            Log.e(TAG, "run failed", e)
            finish(label, DriveResult(Outcome.FAILED, detail = "${e.javaClass.simpleName}: ${e.message}"))
        } finally {
            a11y?.keepScreenOn(false)
            Notifier.cancelUnlock(this)
            if (wake.isHeld) wake.release()
            // Next week's window. A rehearsal changes nothing, so nothing to re-arm.
            if (!rehearsal) BookingScheduler.rescheduleAll(this)
        }
    }

    /**
     * An accessibility service cannot act behind a secure lock screen, so wake the
     * screen and ask for an unlock, then wait for it - up to a minute past the
     * window, since a late booking still beats none.
     */
    private fun ensureUnlocked(label: String, race: Race, clock: TrueClock, rehearsal: Boolean): Boolean {
        val keyguard = getSystemService(KeyguardManager::class.java)
        if (!keyguard.isKeyguardLocked) return true

        Notifier.unlockPrompt(this, label)
        // Allowed from the background because the accessibility service is bound.
        runCatching { startActivity(Intent(this, UnlockActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }

        val deadline = if (rehearsal) clock.now() + 60_000 else race.opensAt.toInstant().toEpochMilli() + 60_000
        while (keyguard.isKeyguardLocked && clock.now() < deadline) Thread.sleep(250)
        Notifier.cancelUnlock(this)
        return !keyguard.isKeyguardLocked
    }

    private fun finish(label: String, result: DriveResult) {
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
        Notifier.result(this, label, result)
    }

    companion object {
        private const val TAG = "phantom-tap"
        private const val EXTRA_REHEARSAL = "rehearsal"
        // Two minutes early + 90s of waiting + seats, with margin.
        private const val WAKE_LIMIT_MS = 10 * 60 * 1000L

        fun start(context: Context, watchId: String, rehearsal: Boolean) {
            val intent = Intent(context, BookingService::class.java)
                .putExtra(BookingScheduler.EXTRA_WATCH, watchId)
                .putExtra(EXTRA_REHEARSAL, rehearsal)
            context.startForegroundService(intent)
        }
    }
}

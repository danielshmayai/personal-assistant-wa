package com.phantomtap.app

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import com.phantomtap.core.Booking
import com.phantomtap.core.BookingStatus
import com.phantomtap.core.FavoriteCard
import com.phantomtap.core.Reconciler

/**
 * One alarm per pending booking, two minutes before its window.
 *
 * setAlarmClock rather than setExactAndAllowWhileIdle: it is the one alarm type
 * Android delivers on time even in deep Doze and on Samsung's aggressive battery
 * management, because to the system it is a wake-up alarm the user relies on.
 * (It shows the alarm icon in the status bar, which is honest - it is one.)
 */
object BookingScheduler {
    const val EXTRA_KEY = "booking_key"
    private const val ACTION_RACE = "com.phantomtap.RACE"

    fun rescheduleAll(context: Context) {
        if (!Automation.isEnabled(context)) return cancelAll(context)
        val now = System.currentTimeMillis()
        for (booking in BookingStore(context).all()) {
            val stillDue = booking.giveUpAt.toInstant().toEpochMilli() > now
            if (booking.status == BookingStatus.PENDING && stillDue) schedule(context, booking) else cancel(context, booking.key)
        }
    }

    fun canScheduleExact(context: Context): Boolean =
        Build.VERSION.SDK_INT < 31 || context.getSystemService(AlarmManager::class.java).canScheduleExactAlarms()

    private fun schedule(context: Context, booking: Booking): Boolean {
        if (!canScheduleExact(context)) {
            Notifier.problem(context, "אין הרשאה להתראות מדויקות", "פתח את phantom-tap ואשר 'התראות ושעונים מעוררים'")
            return false
        }
        // Never in the past: a window that opened seconds ago is still raced.
        val at = maxOf(booking.alarmAt.toInstant().toEpochMilli(), System.currentTimeMillis() + 1_000)
        val show = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        context.getSystemService(AlarmManager::class.java)
            .setAlarmClock(AlarmManager.AlarmClockInfo(at, show), alarmIntent(context, booking.key))
        return true
    }

    fun cancelAll(context: Context) {
        BookingStore(context).all().forEach { cancel(context, it.key) }
    }

    fun cancel(context: Context, key: String) {
        context.getSystemService(AlarmManager::class.java).cancel(alarmIntent(context, key))
    }

    /**
     * One PendingIntent per booking. The key goes in the data URI, not only in an
     * extra: PendingIntents are told apart by action and data, never extras, so two
     * bookings would otherwise overwrite each other's alarm.
     */
    private fun alarmIntent(context: Context, key: String): PendingIntent = PendingIntent.getBroadcast(
        context,
        0,
        Intent(context, AlarmReceiver::class.java)
            .setAction(ACTION_RACE)
            .setData(Uri.parse("phantomtap://booking/${Uri.encode(key)}"))
            .putExtra(EXTRA_KEY, key),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )
}

/** Apply what was read in the favourites: store it, re-arm alarms, tell the user what is new. */
object FavoritesSync {
    fun apply(context: Context, seen: List<FavoriteCard>, complete: Boolean): Reconciler.Outcome {
        val outcome = BookingStore(context).reconcile(seen, complete)
        // A removed booking is no longer in the store, so rescheduleAll cannot see it.
        outcome.removed.forEach { BookingScheduler.cancel(context, it.key) }
        BookingScheduler.rescheduleAll(context)
        val added = outcome.added.filter { it.status == BookingStatus.PENDING }
        if (added.isNotEmpty()) Notifier.added(context, added)
        return outcome
    }
}

/** The alarm went off: start the booking run. */
class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val key = intent.getStringExtra(BookingScheduler.EXTRA_KEY) ?: return
        if (!Automation.isEnabled(context)) return // an alarm that slipped past a cancel
        BookingService.race(context, key, rehearsal = false)
    }
}

/** Alarms do not survive a reboot or a clock change; re-arm them. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        // Exported, so any app could send an explicit intent here. Act only on the
        // system broadcasts this receiver exists for.
        if (intent.action !in SYSTEM_EVENTS) return
        BookingScheduler.rescheduleAll(context)
    }

    private companion object {
        val SYSTEM_EVENTS = setOf(
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
            Intent.ACTION_TIME_CHANGED,
            Intent.ACTION_TIMEZONE_CHANGED,
            "android.app.action.SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED",
        )
    }
}

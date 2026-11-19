package com.phantomtap.app

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import com.phantomtap.core.Race
import com.phantomtap.core.Schedule
import java.time.ZonedDateTime

/**
 * One alarm per watched class, two minutes before its window.
 *
 * setAlarmClock rather than setExactAndAllowWhileIdle: it is the one alarm type
 * Android delivers on time even in deep Doze and on Samsung's aggressive battery
 * management, because to the system it is a wake-up alarm the user relies on.
 * (It shows the alarm icon in the status bar, which is honest - it is one.)
 */
object BookingScheduler {
    const val EXTRA_WATCH = "watch_id"
    private const val ACTION_RACE = "com.phantomtap.RACE"

    fun rescheduleAll(context: Context) {
        val now = ZonedDateTime.now(Schedule.ISRAEL)
        for (watch in WatchStore(context).all()) {
            if (watch.enabled) schedule(context, Schedule.nextRace(watch, now)) else cancel(context, watch.id)
        }
    }

    fun canScheduleExact(context: Context): Boolean =
        Build.VERSION.SDK_INT < 31 || context.getSystemService(AlarmManager::class.java).canScheduleExactAlarms()

    fun schedule(context: Context, race: Race): Boolean {
        if (!canScheduleExact(context)) {
            Notifier.problem(context, "אין הרשאה להתראות מדויקות", "פתח את phantom-tap ואשר 'התראות ושעונים מעוררים'")
            return false
        }
        // Never in the past: a window that opened seconds ago is still raced.
        val at = maxOf(race.alarmAt.toInstant().toEpochMilli(), System.currentTimeMillis() + 1_000)
        val show = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        context.getSystemService(AlarmManager::class.java)
            .setAlarmClock(AlarmManager.AlarmClockInfo(at, show), alarmIntent(context, race.watch.id))
        return true
    }

    fun cancel(context: Context, watchId: String) {
        context.getSystemService(AlarmManager::class.java).cancel(alarmIntent(context, watchId))
    }

    /**
     * One PendingIntent per watch. The watch id goes in the data URI, not only in
     * an extra: PendingIntents are told apart by action and data, never extras,
     * so two watches would otherwise overwrite each other's alarm.
     */
    private fun alarmIntent(context: Context, watchId: String): PendingIntent = PendingIntent.getBroadcast(
        context,
        0,
        Intent(context, AlarmReceiver::class.java)
            .setAction(ACTION_RACE)
            .setData(Uri.parse("phantomtap://watch/${Uri.encode(watchId)}"))
            .putExtra(EXTRA_WATCH, watchId),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )
}

/** The alarm went off: start the booking run. */
class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getStringExtra(BookingScheduler.EXTRA_WATCH) ?: return
        BookingService.start(context, id, rehearsal = false)
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

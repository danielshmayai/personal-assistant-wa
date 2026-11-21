package com.phantomtap.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import com.phantomtap.R
import com.phantomtap.core.Booking
import com.phantomtap.core.DriveResult
import com.phantomtap.core.Outcome
import java.time.format.DateTimeFormatter

/** Every message the user sees outside the app. Hebrew, short, and actionable. */
object Notifier {
    private const val CH_ALERTS = "alerts"
    private const val CH_SERVICE = "service"

    const val ID_SERVICE = 1
    private const val ID_UNLOCK = 2
    private const val ID_PROBLEM = 3
    private const val ID_ADDED = 4
    private val WHEN: DateTimeFormatter = DateTimeFormatter.ofPattern("dd/MM HH:mm")
    private var resultId = 100

    fun ensureChannels(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH_ALERTS, "הרשמות", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "תוצאות הרשמה ובקשות לפתוח את הנעילה"
            },
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_SERVICE, "הרשמה פעילה", NotificationManager.IMPORTANCE_LOW).apply {
                description = "מוצג בזמן שההרשמה מתבצעת"
            },
        )
    }

    private fun openApp(context: Context): PendingIntent = PendingIntent.getActivity(
        context,
        0,
        Intent(context, MainActivity::class.java),
        PendingIntent.FLAG_IMMUTABLE,
    )

    fun service(context: Context, label: String): Notification =
        Notification.Builder(context, CH_SERVICE)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("נרשם ל$label")
            .setContentText("phantom-tap מפעיל את אפליקציית הולמס פלייס")
            .setOngoing(true)
            .setContentIntent(openApp(context))
            .build()

    /**
     * Before the window: light the screen and ask for an unlock.
     *
     * An accessibility service cannot act behind a secure lock screen, so a
     * locked phone at the opening minute means no booking. The full-screen
     * intent wakes the screen over the lock screen and shows the unlock prompt.
     */
    fun unlockPrompt(context: Context, label: String) {
        ensureChannels(context)
        val fullScreen = PendingIntent.getActivity(
            context,
            ID_UNLOCK,
            Intent(context, UnlockActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = Notification.Builder(context, CH_ALERTS)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("פתח את הנעילה")
            .setContentText("ההרשמה ל$label נפתחת בעוד כשתי דקות")
            .setCategory(Notification.CATEGORY_ALARM)
            .setFullScreenIntent(fullScreen, true)
            .setContentIntent(fullScreen)
            .setAutoCancel(true)
            .build()
        context.getSystemService(NotificationManager::class.java).notify(ID_UNLOCK, notification)
    }

    fun cancelUnlock(context: Context) {
        context.getSystemService(NotificationManager::class.java).cancel(ID_UNLOCK)
    }

    fun result(context: Context, label: String, result: DriveResult) {
        ensureChannels(context)
        val (title, text) = when (result.outcome) {
            Outcome.BOOKED -> "✅ נרשמת ל$label" to (result.seat?.let { "מקום $it" } ?: result.detail)
            Outcome.WAITLISTED -> "⏳ $label מלא" to "נכנסת לרשימת ההמתנה"
            Outcome.FULL -> "❌ $label מלא" to "רשימת ההמתנה כבויה בהגדרות השיעור"
            Outcome.SEAT_UNAVAILABLE -> "⚠️ $label: אין מקום מהרשימה שלך" to "כדאי לבדוק באפליקציה אם ההרשמה הושלמה"
            Outcome.NOT_OPENED -> "❌ ההרשמה ל$label לא נפתחה" to "הכפתור נשאר 'תזכורת' גם 90 שניות אחרי המועד"
            Outcome.NOT_FOUND -> "❌ $label לא נמצא במועדפים" to result.detail
            Outcome.REHEARSED -> "🧪 בדיקה ל$label הצליחה" to result.detail
            Outcome.FAILED -> "❌ ההרשמה ל$label נכשלה" to result.detail
        }
        post(context, resultId++, title, text)
    }

    /** New favourites picked up - so the user knows each heart was seen. */
    fun added(context: Context, bookings: List<Booking>) {
        ensureChannels(context)
        val title = if (bookings.size == 1) "נוסף שיעור מהמועדפים" else "נוספו ${bookings.size} שיעורים מהמועדפים"
        val text = bookings.joinToString("\n") { b ->
            "${b.label} · ההרשמה נפתחת ${WHEN.format(b.opensAt)}"
        }
        post(context, ID_ADDED, title, text)
    }

    fun problem(context: Context, title: String, text: String) {
        ensureChannels(context)
        post(context, ID_PROBLEM, title, text)
    }

    private fun post(context: Context, id: Int, title: String, text: String) {
        val notification = Notification.Builder(context, CH_ALERTS)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setContentIntent(openApp(context))
            .setAutoCancel(true)
            .build()
        context.getSystemService(NotificationManager::class.java).notify(id, notification)
    }
}

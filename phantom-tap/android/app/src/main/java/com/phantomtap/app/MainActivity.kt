package com.phantomtap.app

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.app.NotificationManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import com.phantomtap.core.Booking
import com.phantomtap.core.BookingStatus
import com.phantomtap.core.Labels
import com.phantomtap.core.Schedule
import com.phantomtap.core.SeatFallback
import com.phantomtap.core.SeatPrefs
import java.time.Instant
import java.time.format.DateTimeFormatter

/**
 * Setup and status. Classes are not entered here: they come from the ❤️
 * favourites in the Holmes Place app. Built in code rather than XML - one screen,
 * a few lists, and every label in Hebrew next to the logic that uses it.
 */
class MainActivity : Activity() {
    private val bookings by lazy { BookingStore(this) }
    private val seatPrefs by lazy { SeatPrefsStore(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Notifier.ensureChannels(this)
        window.decorView.layoutDirection = View.LAYOUT_DIRECTION_RTL
        if (Build.VERSION.SDK_INT >= 33 && !notificationsAllowed()) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
    }

    override fun onResume() {
        super.onResume()
        BookingScheduler.rescheduleAll(this)
        render()
    }

    // ----------------------------------------------------------------- screen --

    private fun render() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(32))
        }
        root.addView(text("phantom-tap", 24f, bold = true))
        root.addView(
            text(
                "סמן ❤️ בשיעור באפליקציה של הולמס פלייס, ו-phantom-tap יירשם אליו ברגע שההרשמה נפתחת " +
                    "ויבחר מקום לפי הסדר שלך. רק שיעורים מהמועדפים.",
                14f,
            ),
        )

        root.addView(heading("מוכנות"))
        checks().forEach { root.addView(it) }

        root.addView(heading("שיעורים מהמועדפים"))
        root.addView(button("סנכרון מועדפים") { syncNow() })
        root.addView(
            text(
                "הסנכרון פותח את הולמס פלייס וקורא את לשונית המועדפים. הוא קורה גם אחרי כל הרשמה, " +
                    "וגם בכל פעם שאתה עצמך מסתכל על המועדפים.",
                13f,
            ),
        )
        val all = bookings.all()
        val upcoming = all.filter { it.status == BookingStatus.PENDING }.sortedBy { it.opensAt }
        val others = all.filter { it.status != BookingStatus.PENDING }.sortedByDescending { it.classStart }
        if (all.isEmpty()) root.addView(text("עוד אין שיעורים. סמן ❤️ באפליקציה של הולמס פלייס ולחץ 'סנכרון מועדפים'.", 14f))
        upcoming.forEach { root.addView(pendingRow(it)) }
        others.forEach { root.addView(doneRow(it)) }

        root.addView(heading("סדר מקומות - לכל השיעורים"))
        root.addView(text(seatSummary(seatPrefs.get()), 15f))
        root.addView(button("עריכת מקומות") { editSeats() })

        root.addView(heading("היסטוריה"))
        val history = History(this).recent(10)
        if (history.isEmpty()) root.addView(text("עוד לא היו הרשמות.", 14f))
        history.forEach { entry -> root.addView(historyRow(entry)) }

        setContentView(ScrollView(this).apply { addView(root) })
    }

    private fun checks(): List<View> {
        val pkg = Uri.parse("package:$packageName")
        val rows = mutableListOf(
            check("אפליקציית הולמס פלייס מותקנת", packageManager.getLaunchIntentForPackage(Labels.PACKAGE) != null, null, null),
            check("שירות הנגישות של phantom-tap פעיל", HolmesA11yService.isEnabled(this), "הפעלה") {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            },
            check("מותר לקבוע התראות מדויקות", BookingScheduler.canScheduleExact(this), "אישור") {
                if (Build.VERSION.SDK_INT >= 31) startActivity(Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, pkg))
            },
            check("מותר להציג התראות", notificationsAllowed(), "אישור") {
                if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
            },
            check("ללא הגבלת סוללה", ignoringBatteryLimits(), "אישור") {
                startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, pkg))
            },
        )
        if (Build.VERSION.SDK_INT >= 34) {
            val allowed = getSystemService(NotificationManager::class.java).canUseFullScreenIntent()
            rows += check("מותר להדליק מסך מעל מסך הנעילה", allowed, "אישור") {
                startActivity(Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, pkg))
            }
        }
        rows += text(
            "חשוב: שתי דקות לפני פתיחת ההרשמה הטלפון יידלק ויבקש לפתוח את הנעילה. " +
                "אם הטלפון יישאר נעול, אי אפשר להירשם.",
            13f,
        )
        return rows
    }

    private fun pendingRow(booking: Booking): View {
        val source = if (booking.opensAtFromCard) "" else " (משוער: 5 שעות לפני)"
        val summary = "${booking.label}\nההרשמה נפתחת: ${WHEN.format(booking.opensAt)}$source"
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(8), 0, dp(8))
            addView(text(summary, 15f))
            addView(
                LinearLayout(this@MainActivity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    addView(button("בדיקה") { rehearse(booking) })
                    addView(button("לא להירשם") { skip(booking) })
                },
            )
        }
    }

    private fun doneRow(booking: Booking): View {
        val status = when (booking.status) {
            BookingStatus.BOOKED -> "✅ נרשמת"
            BookingStatus.WAITLISTED -> "⏳ ברשימת המתנה"
            BookingStatus.MISSED -> "❌ לא נרשמת"
            BookingStatus.SKIPPED -> "⏭️ לא נרשם"
            BookingStatus.PENDING -> ""
        }
        val detail = if (booking.detail.isNotEmpty()) " · ${booking.detail}" else ""
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(6), 0, dp(6))
            addView(text("$status · ${booking.label}$detail", 14f))
            if (booking.status == BookingStatus.SKIPPED && booking.classStart.isAfter(java.time.ZonedDateTime.now(Schedule.ISRAEL))) {
                addView(button("כן להירשם") { restore(booking) })
            }
        }
    }

    private fun historyRow(entry: History.Entry): View {
        val at = WHEN.format(Instant.ofEpochMilli(entry.atMs).atZone(Schedule.ISRAEL))
        val seat = entry.seat?.let { " · מקום $it" } ?: ""
        val line = "$at · ${entry.label} · ${entry.outcome}$seat" + if (entry.detail.isNotEmpty()) "\n${entry.detail}" else ""
        return text(line, 13f).apply {
            setPadding(0, dp(6), 0, dp(6))
            // The step log is how a missed booking gets explained; one tap away.
            setOnClickListener {
                AlertDialog.Builder(this@MainActivity)
                    .setTitle(entry.label)
                    .setMessage(entry.steps.joinToString("\n"))
                    .setPositiveButton("סגירה", null)
                    .show()
            }
        }
    }

    // ---------------------------------------------------------------- actions --

    private fun syncNow() {
        if (!HolmesA11yService.isEnabled(this)) {
            toast("קודם צריך להפעיל את שירות הנגישות")
            return
        }
        toast("פותח את הולמס פלייס וקורא את המועדפים")
        BookingService.sync(this)
    }

    private fun rehearse(booking: Booking) {
        if (!HolmesA11yService.isEnabled(this)) {
            toast("קודם צריך להפעיל את שירות הנגישות")
            return
        }
        toast("בדיקה: פותח את הולמס פלייס ומגיע לשיעור במועדפים, בלי להירשם")
        BookingService.race(this, booking.key, rehearsal = true)
    }

    /**
     * Not just deleted: a class still hearted would come straight back on the next
     * sync. Marked skipped, it stays known, so the sync leaves it alone.
     */
    private fun skip(booking: Booking) {
        bookings.update(booking.copy(status = BookingStatus.SKIPPED, detail = "בוטל ידנית"))
        BookingScheduler.cancel(this, booking.key)
        render()
    }

    private fun restore(booking: Booking) {
        bookings.update(booking.copy(status = BookingStatus.PENDING, detail = ""))
        BookingScheduler.rescheduleAll(this)
        render()
    }

    private fun editSeats() {
        val current = seatPrefs.get()
        val seats = EditText(this).apply {
            hint = "מקומות לפי סדר עדיפות, למשל 12, 13, 11"
            setText(current.preferredSeats.joinToString(", "))
        }
        val fallback = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, FALLBACKS.map { it.second })
            setSelection(FALLBACKS.indexOfFirst { it.first == current.fallback })
        }
        val waitlist = CheckBox(this).apply {
            text = "אם השיעור מלא - להירשם לרשימת המתנה"
            isChecked = current.joinWaitlist
        }
        val form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
            listOf(label("מקומות מועדפים, לכל השיעורים"), seats, label("אם כולם תפוסים"), fallback, waitlist).forEach(::addView)
        }
        val dialog = AlertDialog.Builder(this)
            .setTitle("סדר מקומות")
            .setView(ScrollView(this).apply { addView(form) })
            .setPositiveButton("שמירה", null)
            .setNegativeButton("ביטול", null)
            .create()
        dialog.setOnShowListener {
            // Validate before closing: a bad entry keeps the dialog open with its text.
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                runCatching {
                    SeatPrefs(
                        preferredSeats = parseSeats(seats.text.toString()),
                        fallback = FALLBACKS[fallback.selectedItemPosition].first,
                        joinWaitlist = waitlist.isChecked,
                    )
                }.onSuccess {
                    seatPrefs.set(it)
                    dialog.dismiss()
                    render()
                }.onFailure { toast("לא תקין: ${it.message}") }
            }
        }
        dialog.show()
    }

    // ---------------------------------------------------------------- helpers --

    private fun seatSummary(prefs: SeatPrefs): String {
        val seats = if (prefs.preferredSeats.isEmpty()) "כל מקום" else prefs.preferredSeats.joinToString(", ")
        val fallback = FALLBACKS.first { it.first == prefs.fallback }.second
        return "מקומות: $seats\nאם כולם תפוסים: $fallback\nאם מלא: ${if (prefs.joinWaitlist) "רשימת המתנה" else "לא להירשם"}"
    }

    private fun notificationsAllowed(): Boolean = Build.VERSION.SDK_INT < 33 ||
        checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun ignoringBatteryLimits(): Boolean =
        getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)

    private fun check(title: String, ok: Boolean, fix: String?, onFix: (() -> Unit)?): View =
        LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            addView(text("${if (ok) "✅" else "❌"} $title", 15f).apply {
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            })
            if (!ok && fix != null && onFix != null) addView(button(fix) { onFix() })
        }

    private fun heading(s: String) = text(s, 18f, bold = true).apply { setPadding(0, dp(20), 0, dp(6)) }

    private fun label(s: String) = text(s, 13f).apply { setPadding(0, dp(10), 0, 0) }

    private fun text(s: String, size: Float, bold: Boolean = false) = TextView(this).apply {
        text = s
        textSize = size
        if (bold) setTypeface(typeface, Typeface.BOLD)
    }

    private fun button(s: String, onClick: () -> Unit) = Button(this).apply {
        text = s
        isAllCaps = false
        setOnClickListener { onClick() }
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_LONG).show()

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    companion object {
        private val FALLBACKS = listOf(
            SeatFallback.NEAREST to "המקום הפנוי הקרוב ביותר",
            SeatFallback.ANY to "כל מקום פנוי",
            SeatFallback.NONE to "רק מהרשימה שלי",
        )
        private val WHEN: DateTimeFormatter = DateTimeFormatter.ofPattern("dd/MM HH:mm")

        fun parseSeats(text: String): List<Int> =
            text.split(Regex("[,\\s]+")).filter { it.isNotBlank() }.map { it.trim().toInt() }
    }
}

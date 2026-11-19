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
import android.text.InputType
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
import com.phantomtap.core.Labels
import com.phantomtap.core.Schedule
import com.phantomtap.core.SeatFallback
import com.phantomtap.core.Watch
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalTime
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.UUID

/**
 * Setup and status. Built in code rather than XML: one screen, a few lists, and
 * every label in Hebrew right here next to the logic that uses it.
 */
class MainActivity : Activity() {
    private val store by lazy { WatchStore(this) }

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
        root.addView(text("נרשם לשיעורים באפליקציית הולמס פלייס ברגע שההרשמה נפתחת, ובוחר מקום לפי הסדר שלך.", 14f))

        root.addView(heading("מוכנות"))
        checks().forEach { root.addView(it) }

        root.addView(heading("שיעורים"))
        val watches = store.all().sortedWith(compareBy({ dayIndex(it.dayOfWeek) }, { it.start }))
        if (watches.isEmpty()) root.addView(text("עוד אין שיעורים. הוסף את הראשון.", 14f))
        watches.forEach { root.addView(watchRow(it)) }
        root.addView(button("הוסף שיעור") { editDialog(null) })

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

    private fun watchRow(watch: Watch): View {
        val race = Schedule.nextRace(watch, ZonedDateTime.now(Schedule.ISRAEL))
        val seats = if (watch.preferredSeats.isEmpty()) "כל מקום" else watch.preferredSeats.joinToString(", ")
        val summary = buildString {
            append("${watch.className} · ${dayName(watch.dayOfWeek)} ${watch.startLabel}")
            if (!watch.enabled) append(" · מושבת")
            append("\nמקומות: $seats · אם תפוסים: ${fallbackName(watch.seatFallback)}")
            append(" · המתנה: ${if (watch.joinWaitlist) "כן" else "לא"}")
            if (watch.enabled) append("\nההרשמה הבאה נפתחת: ${WHEN.format(race.opensAt)}")
        }
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(8), 0, dp(8))
            addView(text(summary, 15f))
            addView(
                LinearLayout(this@MainActivity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    addView(button("בדיקה") { rehearse(watch) })
                    addView(button("עריכה") { editDialog(watch) })
                    addView(button(if (watch.enabled) "השבתה" else "הפעלה") { toggle(watch) })
                    addView(button("מחיקה") { confirmDelete(watch) })
                },
            )
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

    private fun rehearse(watch: Watch) {
        if (!HolmesA11yService.isEnabled(this)) {
            toast("קודם צריך להפעיל את שירות הנגישות")
            return
        }
        toast("בדיקה: פותח את הולמס פלייס ומגיע לשיעור, בלי להירשם")
        BookingService.start(this, watch.id, rehearsal = true)
    }

    private fun toggle(watch: Watch) {
        store.put(watch.copy(enabled = !watch.enabled))
        BookingScheduler.rescheduleAll(this)
        render()
    }

    private fun confirmDelete(watch: Watch) {
        AlertDialog.Builder(this)
            .setMessage("למחוק את ${watch.className} ${watch.startLabel}?")
            .setPositiveButton("מחיקה") { _, _ ->
                BookingScheduler.cancel(this, watch.id)
                store.remove(watch.id)
                render()
            }
            .setNegativeButton("ביטול", null)
            .show()
    }

    private fun editDialog(existing: Watch?) {
        val name = EditText(this).apply {
            hint = "שם השיעור, כמו באפליקציה (למשל BODY POWER)"
            setText(existing?.className.orEmpty())
        }
        val day = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, DAYS.map { it.second })
            setSelection(existing?.let { dayIndex(it.dayOfWeek) } ?: 0)
        }
        val start = EditText(this).apply {
            hint = "שעת תחילת השיעור, למשל 19:10"
            inputType = InputType.TYPE_CLASS_DATETIME or InputType.TYPE_DATETIME_VARIATION_TIME
            setText(existing?.startLabel.orEmpty())
        }
        val seats = EditText(this).apply {
            hint = "מקומות לפי סדר עדיפות, למשל 12, 13, 11"
            setText(existing?.preferredSeats?.joinToString(", ").orEmpty())
        }
        val fallback = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, FALLBACKS.map { it.second })
            setSelection(FALLBACKS.indexOfFirst { it.first == (existing?.seatFallback ?: SeatFallback.NEAREST) })
        }
        val waitlist = CheckBox(this).apply {
            text = "אם השיעור מלא - להירשם לרשימת המתנה"
            isChecked = existing?.joinWaitlist ?: true
        }
        val lead = EditText(this).apply {
            hint = "כמה שעות לפני השיעור ההרשמה נפתחת"
            inputType = InputType.TYPE_CLASS_NUMBER
            setText((existing?.opensHoursBefore ?: 5).toString())
        }
        val form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
            listOf(
                label("שיעור"), name, label("יום"), day, label("שעה"), start,
                label("מקומות מועדפים"), seats, label("אם כולם תפוסים"), fallback, waitlist,
                label("ההרשמה נפתחת לפני השיעור (שעות; בכל השיעורים שראינו: 5)"), lead,
            ).forEach(::addView)
        }

        val dialog = AlertDialog.Builder(this)
            .setTitle(if (existing == null) "שיעור חדש" else "עריכת שיעור")
            .setView(ScrollView(this).apply { addView(form) })
            .setPositiveButton("שמירה", null)
            .setNegativeButton("ביטול", null)
            .create()
        dialog.setOnShowListener {
            // Validate before closing: a bad entry keeps the dialog open with its text.
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                runCatching {
                    Watch(
                        id = existing?.id ?: UUID.randomUUID().toString(),
                        className = name.text.toString().trim(),
                        dayOfWeek = DAYS[day.selectedItemPosition].first,
                        start = LocalTime.parse(start.text.toString().trim(), TIME_INPUT),
                        preferredSeats = parseSeats(seats.text.toString()),
                        seatFallback = FALLBACKS[fallback.selectedItemPosition].first,
                        joinWaitlist = waitlist.isChecked,
                        opensHoursBefore = lead.text.toString().trim().toInt(),
                        enabled = existing?.enabled ?: true,
                    )
                }.onSuccess { watch ->
                    store.put(watch)
                    BookingScheduler.rescheduleAll(this)
                    dialog.dismiss()
                    render()
                }.onFailure { toast("לא תקין: ${it.message}") }
            }
        }
        dialog.show()
    }

    // ---------------------------------------------------------------- helpers --

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
        // The Israeli week starts on Sunday, and so does this list.
        private val DAYS = listOf(
            DayOfWeek.SUNDAY to "ראשון",
            DayOfWeek.MONDAY to "שני",
            DayOfWeek.TUESDAY to "שלישי",
            DayOfWeek.WEDNESDAY to "רביעי",
            DayOfWeek.THURSDAY to "חמישי",
            DayOfWeek.FRIDAY to "שישי",
            DayOfWeek.SATURDAY to "שבת",
        )
        private val FALLBACKS = listOf(
            SeatFallback.NEAREST to "המקום הפנוי הקרוב ביותר",
            SeatFallback.ANY to "כל מקום פנוי",
            SeatFallback.NONE to "רק מהרשימה שלי",
        )
        private val WHEN: DateTimeFormatter = DateTimeFormatter.ofPattern("dd/MM HH:mm")
        private val TIME_INPUT: DateTimeFormatter = DateTimeFormatter.ofPattern("H:mm")

        private fun dayIndex(day: DayOfWeek) = DAYS.indexOfFirst { it.first == day }
        private fun dayName(day: DayOfWeek) = DAYS.first { it.first == day }.second
        private fun fallbackName(f: SeatFallback) = FALLBACKS.first { it.first == f }.second

        fun parseSeats(text: String): List<Int> =
            text.split(Regex("[,\\s]+")).filter { it.isNotBlank() }.map { it.trim().toInt() }
    }
}

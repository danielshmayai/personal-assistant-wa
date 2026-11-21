package com.phantomtap.app

import android.content.Context
import com.phantomtap.core.Booking
import com.phantomtap.core.BookingStatus
import com.phantomtap.core.FavoriteCard
import com.phantomtap.core.Reconciler
import com.phantomtap.core.Schedule
import com.phantomtap.core.SeatFallback
import com.phantomtap.core.SeatPrefs
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZonedDateTime

/**
 * The classes to book, discovered from the Holmes Place favourites.
 *
 * Three writers share this - a booking run, a passive read while the user
 * browses the favourites, and the app screen - so every read-modify-write goes
 * through [edit] under one lock. Without it, a passive read finishing during a
 * run would silently undo the run's result.
 *
 * No Holmes Place credentials are stored anywhere: the app operates the Holmes
 * Place app the user is already logged into.
 */
class BookingStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("phantom_tap", Context.MODE_PRIVATE)

    fun all(): List<Booking> = synchronized(LOCK) { decode(prefs.getString(KEY, null)) }

    fun get(key: String): Booking? = all().firstOrNull { it.key == key }

    /** Read, change, write - atomically with respect to every other writer. */
    fun <T> edit(change: (List<Booking>) -> Pair<List<Booking>, T>): T = synchronized(LOCK) {
        val (next, result) = change(decode(prefs.getString(KEY, null)))
        // commit, not apply: an alarm is armed from this data right after.
        prefs.edit().putString(KEY, encode(next)).commit()
        result
    }

    fun update(booking: Booking) = edit { list -> (list.filterNot { it.key == booking.key } + booking) to Unit }

    fun reconcile(seen: List<FavoriteCard>, complete: Boolean): Reconciler.Outcome = edit { list ->
        val outcome = Reconciler.reconcile(list, seen, complete, ZonedDateTime.now(Schedule.ISRAEL))
        outcome.bookings to outcome
    }

    companion object {
        private const val KEY = "bookings"
        private val LOCK = Any()

        fun encode(bookings: List<Booking>): String = JSONArray().apply {
            bookings.forEach { b ->
                put(
                    JSONObject()
                        .put("className", b.className)
                        .put("date", b.date.toString())
                        .put("start", b.startLabel)
                        .put("opensAt", b.opensAt.toInstant().toEpochMilli())
                        .put("fromCard", b.opensAtFromCard)
                        .put("status", b.status.name)
                        .put("detail", b.detail),
                )
            }
        }.toString()

        /** Entries that no longer parse are dropped rather than taking the rest down with them. */
        fun decode(json: String?): List<Booking> {
            if (json.isNullOrBlank()) return emptyList()
            val array = runCatching { JSONArray(json) }.getOrNull() ?: return emptyList()
            return (0 until array.length()).mapNotNull { i ->
                runCatching {
                    val o = array.getJSONObject(i)
                    Booking(
                        className = o.getString("className"),
                        date = LocalDate.parse(o.getString("date")),
                        start = LocalTime.parse(o.getString("start")),
                        opensAt = Instant.ofEpochMilli(o.getLong("opensAt")).atZone(Schedule.ISRAEL),
                        opensAtFromCard = o.optBoolean("fromCard", true),
                        status = BookingStatus.valueOf(o.optString("status", "PENDING")),
                        detail = o.optString("detail"),
                    )
                }.getOrNull()
            }
        }
    }
}

/** The one seat list for every class, as the user chose. */
class SeatPrefsStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("phantom_tap", Context.MODE_PRIVATE)

    fun get(): SeatPrefs = runCatching {
        val seats = JSONArray(prefs.getString("seats", "[]"))
        SeatPrefs(
            preferredSeats = (0 until seats.length()).map { seats.getInt(it) },
            fallback = SeatFallback.valueOf(prefs.getString("fallback", SeatFallback.NEAREST.name)!!),
            joinWaitlist = prefs.getBoolean("waitlist", true),
        )
    }.getOrDefault(SeatPrefs())

    fun set(value: SeatPrefs) {
        prefs.edit()
            .putString("seats", JSONArray(value.preferredSeats).toString())
            .putString("fallback", value.fallback.name)
            .putBoolean("waitlist", value.joinWaitlist)
            .commit()
    }
}

/** What happened on each run and sync, newest first, so a miss can be explained afterwards. */
class History(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("phantom_tap_history", Context.MODE_PRIVATE)

    data class Entry(
        val atMs: Long,
        val label: String,
        val outcome: String,
        val seat: Int?,
        val detail: String,
        val steps: List<String>,
    )

    fun add(entry: Entry) = synchronized(LOCK) {
        val kept = listOf(entry) + recent(MAX - 1)
        val array = JSONArray()
        kept.forEach { e ->
            array.put(
                JSONObject()
                    .put("at", e.atMs)
                    .put("label", e.label)
                    .put("outcome", e.outcome)
                    .put("seat", e.seat ?: JSONObject.NULL)
                    .put("detail", e.detail)
                    .put("steps", JSONArray(e.steps)),
            )
        }
        prefs.edit().putString(KEY, array.toString()).commit()
        Unit
    }

    fun recent(limit: Int = 20): List<Entry> {
        val array = runCatching { JSONArray(prefs.getString(KEY, "[]")) }.getOrNull() ?: return emptyList()
        return (0 until minOf(limit, array.length())).mapNotNull { i ->
            runCatching {
                val o = array.getJSONObject(i)
                val steps = o.getJSONArray("steps")
                Entry(
                    atMs = o.getLong("at"),
                    label = o.getString("label"),
                    outcome = o.getString("outcome"),
                    seat = if (o.isNull("seat")) null else o.getInt("seat"),
                    detail = o.optString("detail"),
                    steps = (0 until steps.length()).map { steps.getString(it) },
                )
            }.getOrNull()
        }
    }

    private companion object {
        const val KEY = "entries"
        const val MAX = 30
        val LOCK = Any()
    }
}

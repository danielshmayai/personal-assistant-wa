package com.phantomtap.app

import android.content.Context
import com.phantomtap.core.SeatFallback
import com.phantomtap.core.Watch
import org.json.JSONArray
import org.json.JSONObject
import java.time.DayOfWeek
import java.time.LocalTime

/**
 * The classes to book, on the phone.
 *
 * No Holmes Place credentials are stored anywhere: the app operates the Holmes
 * Place app the user is already logged into. What is stored here is only which
 * classes to book and which seats to prefer.
 */
class WatchStore(context: Context) {
    private val prefs = context.getSharedPreferences("phantom_tap", Context.MODE_PRIVATE)

    fun all(): List<Watch> = decode(prefs.getString(KEY, null))

    fun get(id: String): Watch? = all().firstOrNull { it.id == id }

    fun put(watch: Watch) = save(all().filterNot { it.id == watch.id } + watch)

    fun remove(id: String) = save(all().filterNot { it.id == id })

    // commit, not apply: an alarm is scheduled from this data right after.
    private fun save(watches: List<Watch>) {
        prefs.edit().putString(KEY, encode(watches)).commit()
    }

    companion object {
        private const val KEY = "watches"

        fun encode(watches: List<Watch>): String = JSONArray().apply {
            watches.forEach { w ->
                put(
                    JSONObject()
                        .put("id", w.id)
                        .put("className", w.className)
                        .put("day", w.dayOfWeek.name)
                        .put("start", w.startLabel)
                        .put("seats", JSONArray(w.preferredSeats))
                        .put("fallback", w.seatFallback.name)
                        .put("waitlist", w.joinWaitlist)
                        .put("opensHoursBefore", w.opensHoursBefore)
                        .put("enabled", w.enabled),
                )
            }
        }.toString()

        /** Entries that no longer parse are dropped rather than taking the rest down with them. */
        fun decode(json: String?): List<Watch> {
            if (json.isNullOrBlank()) return emptyList()
            val array = runCatching { JSONArray(json) }.getOrNull() ?: return emptyList()
            return (0 until array.length()).mapNotNull { i ->
                runCatching {
                    val o = array.getJSONObject(i)
                    val seats = o.getJSONArray("seats")
                    Watch(
                        id = o.getString("id"),
                        className = o.getString("className"),
                        dayOfWeek = DayOfWeek.valueOf(o.getString("day")),
                        start = LocalTime.parse(o.getString("start")),
                        preferredSeats = (0 until seats.length()).map { seats.getInt(it) },
                        seatFallback = SeatFallback.valueOf(o.optString("fallback", "NEAREST")),
                        joinWaitlist = o.optBoolean("waitlist", true),
                        opensHoursBefore = o.optInt("opensHoursBefore", 5),
                        enabled = o.optBoolean("enabled", true),
                    )
                }.getOrNull()
            }
        }
    }
}

/** What happened on each run, newest first, so a miss can be explained afterwards. */
class History(context: Context) {
    private val prefs = context.getSharedPreferences("phantom_tap_history", Context.MODE_PRIVATE)

    data class Entry(
        val atMs: Long,
        val label: String,
        val outcome: String,
        val seat: Int?,
        val detail: String,
        val steps: List<String>,
    )

    fun add(entry: Entry) {
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
    }
}

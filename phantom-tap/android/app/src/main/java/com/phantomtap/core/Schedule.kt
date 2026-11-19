package com.phantomtap.core

import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime

/** One concrete registration window for a watch. */
data class Race(
    val watch: Watch,
    val date: LocalDate,
    val classStart: ZonedDateTime,
    val opensAt: ZonedDateTime,
) {
    /** When the alarm fires: early enough to unlock, open the app and reach the card. */
    val alarmAt: ZonedDateTime get() = opensAt.minusSeconds(Schedule.ARRIVE_EARLY_SECONDS)

    /** Stop waiting for the button this long after it should have flipped. */
    val giveUpAt: ZonedDateTime get() = opensAt.plusSeconds(Schedule.GIVE_UP_AFTER_SECONDS)

    /** The day strip prints dates as d.M - "19.11" for 19 November. */
    val dayLabel: String get() = "${date.dayOfMonth}.${date.monthValue}"
}

object Schedule {
    val ISRAEL: ZoneId = ZoneId.of("Asia/Jerusalem")

    /**
     * Two minutes is the budget for: the user unlocking the phone when prompted,
     * launching the app, opening the timetable, picking the day and scrolling to
     * the card. All of that happens before the window opens, so none of it costs
     * time at the moment that matters.
     */
    const val ARRIVE_EARLY_SECONDS = 120L

    /** If the button has not flipped 90s after the expected minute, it is not going to. */
    const val GIVE_UP_AFTER_SECONDS = 90L

    /**
     * The registration window to arm for next.
     *
     * The nearest occurrence whose window has not already been missed. A window
     * that opened in the last [GIVE_UP_AFTER_SECONDS] still counts: after a reboot
     * at 14:10:20, the 14:10 window is still worth racing for.
     */
    fun nextRace(watch: Watch, now: ZonedDateTime): Race {
        val local = now.withZoneSameInstant(ISRAEL)
        for (offset in 0L..7L) {
            val date = local.toLocalDate().plusDays(offset)
            if (date.dayOfWeek != watch.dayOfWeek) continue
            val start = ZonedDateTime.of(date, watch.start, ISRAEL)
            val opens = start.minusHours(watch.opensHoursBefore.toLong())
            if (opens.plusSeconds(GIVE_UP_AFTER_SECONDS).isAfter(local)) {
                return Race(watch, date, start, opens)
            }
        }
        // Eight consecutive days always contain the weekday twice; the second is
        // a full week out, which is always in the future. Unreachable.
        error("no occurrence of ${watch.dayOfWeek} in the next eight days")
    }
}

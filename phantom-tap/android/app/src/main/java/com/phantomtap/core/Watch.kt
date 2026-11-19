package com.phantomtap.core

import java.time.DayOfWeek
import java.time.LocalTime

/** What to do when none of the preferred seats is free. */
enum class SeatFallback {
    /** The free seat closest (by number) to the preferred ones. */
    NEAREST,

    /** The first free seat in the list. */
    ANY,

    /** Preferred seats or nothing. The registration itself is still kept. */
    NONE,
}

/**
 * One class to book, every week.
 *
 * Deliberately has no "registration opens at" field typed by the user. The app
 * opens registration a fixed time before the class starts - five hours on every
 * class observed - so the opening moment is derived from the start time, and
 * the driver waits on the card for the button to actually flip rather than
 * trusting the clock alone.
 */
data class Watch(
    val id: String,
    val className: String,
    val dayOfWeek: DayOfWeek,
    val start: LocalTime,
    val preferredSeats: List<Int> = emptyList(),
    val seatFallback: SeatFallback = SeatFallback.NEAREST,
    val joinWaitlist: Boolean = true,
    /** Observed as 5 on every class seen so far; adjustable if a class differs. */
    val opensHoursBefore: Int = 5,
    val enabled: Boolean = true,
) {
    init {
        require(className.isNotBlank()) { "class name is empty" }
        require(opensHoursBefore in 0..48) { "opensHoursBefore must be 0..48, got $opensHoursBefore" }
        require(preferredSeats.toSet().size == preferredSeats.size) { "duplicate seats in $preferredSeats" }
        require(preferredSeats.all { it > 0 }) { "seat numbers start at 1: $preferredSeats" }
    }

    /** "HH:mm", as the card prints it. */
    val startLabel: String get() = "%02d:%02d".format(start.hour, start.minute)
}

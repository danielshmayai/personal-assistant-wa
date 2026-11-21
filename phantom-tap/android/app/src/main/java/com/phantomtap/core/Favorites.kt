package com.phantomtap.core

import java.time.Duration
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZonedDateTime

/** One class occurrence, as its card in the "מועדפים" tab shows it. */
data class FavoriteCard(
    val className: String,
    val date: LocalDate,
    val start: LocalTime,
    /** Read from "פתיחת הרשמה: dd/MM HH:mm"; null when the card does not show it. */
    val opensAt: ZonedDateTime?,
    /** REMINDER, REGISTER or WAITLIST; null for anything else (e.g. already registered). */
    val action: String?,
    /** The card's button text as shown, whatever it is - kept for the record. */
    val actionText: String?,
) {
    val key: String get() = bookingKey(className, date, start)
}

fun bookingKey(className: String, date: LocalDate, start: LocalTime): String =
    "${className.trim()}|$date|${"%02d:%02d".format(start.hour, start.minute)}"

/** Seat preferences: one list for every class, as the user chose. */
data class SeatPrefs(
    val preferredSeats: List<Int> = emptyList(),
    val fallback: SeatFallback = SeatFallback.NEAREST,
    val joinWaitlist: Boolean = true,
) {
    init {
        require(preferredSeats.toSet().size == preferredSeats.size) { "duplicate seats in $preferredSeats" }
        require(preferredSeats.all { it > 0 }) { "seat numbers start at 1: $preferredSeats" }
    }
}

enum class BookingStatus {
    /** Waiting for its window; an alarm is armed. */
    PENDING,
    BOOKED,
    WAITLISTED,

    /** The run ended without a booking - see detail. */
    MISSED,

    /** Not ours to book: the card shows something other than the three known actions. */
    SKIPPED,
}

/** One class occurrence to book, discovered from the favourites. */
data class Booking(
    val className: String,
    val date: LocalDate,
    val start: LocalTime,
    val opensAt: ZonedDateTime,
    /** False when the card did not show the opening time and the 5h rule supplied it. */
    val opensAtFromCard: Boolean,
    val status: BookingStatus = BookingStatus.PENDING,
    val detail: String = "",
) {
    val key: String get() = bookingKey(className, date, start)
    val startLabel: String get() = "%02d:%02d".format(start.hour, start.minute)

    /** The favourites card prints the date as dd/MM. */
    val dateLabel: String get() = "%02d/%02d".format(date.dayOfMonth, date.monthValue)
    val classStart: ZonedDateTime get() = ZonedDateTime.of(date, start, Schedule.ISRAEL)
    val alarmAt: ZonedDateTime get() = opensAt.minusSeconds(Schedule.ARRIVE_EARLY_SECONDS)
    val giveUpAt: ZonedDateTime get() = opensAt.plusSeconds(Schedule.GIVE_UP_AFTER_SECONDS)
    val label: String get() = "$className $dateLabel $startLabel"
}

/**
 * Reads favourites cards off the screen.
 *
 * A card is anchored on its time range ("08:30 - 09:25"): every card has exactly
 * one, while its button text is not known for every state - an already registered
 * card shows something other than the three actions the driver knows. So: the
 * largest region around a range that holds no other range and none of the app's
 * navigation, is one card.
 */
object FavoritesParser {
    private val RANGE = Regex("(\\d{1,2}):(\\d{2})\\s*[-–]\\s*(\\d{1,2}):(\\d{2})")
    private val DATE = Regex("(?<![\\d/])(\\d{1,2})/(\\d{1,2})(?![\\d/])")
    private val TIME = Regex("(?<!\\d)(\\d{1,2}):(\\d{2})(?!\\d)")
    private val ONLY_PUNCTUATION = Regex("^[\\p{Punct}\\s|]+$")
    private val NUMBER = Regex("^\\d+$")
    private const val OPENING = "פתיחת הרשמה"
    private val DAY_NAMES = setOf("ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "היום", "מחר")
    private val CHROME = setOf(Labels.TAB_STUDIO, Labels.SUBTAB_FAVORITES, Labels.SUBTAB_BOOKED, Labels.SUBTAB_SCHEDULE)

    /** Is the favourites sub-tab the one showing? Needs the app to expose selection state. */
    fun favoritesShowing(root: Node): Boolean =
        root.find { normalize(it.label) == Labels.SUBTAB_FAVORITES && it.selected } != null

    /** Every card on screen, each with its node so the driver can act on it. */
    fun cards(root: Node, today: LocalDate): List<Pair<FavoriteCard, Node>> =
        cardNodes(root).mapNotNull { node -> parse(node, today)?.let { it to node } }

    fun cardNodes(root: Node): List<Node> {
        val anchors = root.descendants().filter { n -> n.label?.let { RANGE.containsMatchIn(it) } == true }.toList()
        return anchors.map { anchor ->
            var card = anchor
            while (true) {
                val parent = card.parent ?: break
                if (parent.scrollable || rangeCount(parent) > 1 || hasChrome(parent)) break
                card = parent
            }
            card
        }.distinct()
    }

    fun parse(card: Node, today: LocalDate): FavoriteCard? {
        val labels = card.descendants().mapNotNull { it.label }.toList()
        val headerIndex = labels.indexOfFirst { RANGE.containsMatchIn(it) }
        if (headerIndex < 0) return null
        val range = RANGE.find(labels[headerIndex])!!.groupValues
        val a = LocalTime.of(range[1].toInt(), range[2].toInt())
        val b = LocalTime.of(range[3].toInt(), range[4].toInt())
        // Right-to-left text may put the range either way round; a class starts first.
        val start = minOf(a, b)

        val openingIndex = labels.indexOfFirst { it.contains(OPENING) }
        val date = labels.withIndex()
            // The opening line carries a date too; the class date is the other one.
            .filter { (i, _) -> openingIndex < 0 || (i != openingIndex && i != openingIndex + 1) }
            .firstNotNullOfOrNull { (_, l) -> DATE.find(l) }
            ?.let { inferYear(it.groupValues[1].toInt(), it.groupValues[2].toInt(), today) }
            ?: return null

        val name = labels.drop(headerIndex + 1).firstOrNull { isName(it) } ?: return null

        val opensAt = if (openingIndex >= 0) {
            // The value may sit in the same label or in the next one.
            val text = labels[openingIndex] + " " + labels.getOrElse(openingIndex + 1) { "" }
            val d = DATE.find(text)
            val t = TIME.find(text)
            if (d != null && t != null) {
                inferYear(d.groupValues[1].toInt(), d.groupValues[2].toInt(), today)?.let { day ->
                    ZonedDateTime.of(day, LocalTime.of(t.groupValues[1].toInt(), t.groupValues[2].toInt()), Schedule.ISRAEL)
                }
            } else {
                null
            }
        } else {
            null
        }

        val action = when {
            card.hasExact(Labels.WAITLIST) -> Labels.WAITLIST
            card.hasExact(Labels.REGISTER) -> Labels.REGISTER
            card.hasExact(Labels.REMINDER) -> Labels.REMINDER
            else -> null
        }
        val actionText = action ?: card.descendants().lastOrNull { it.clickable && it.label != null }?.label
        return FavoriteCard(name.trim(), date, start, opensAt, action, actionText)
    }

    private fun isName(label: String): Boolean {
        val l = label.trim()
        return l.isNotEmpty() &&
            !RANGE.containsMatchIn(l) &&
            !NUMBER.matches(l) &&
            !ONLY_PUNCTUATION.matches(l) &&
            !DATE.matches(l) &&
            l !in DAY_NAMES &&
            !l.contains(OPENING) &&
            l != Labels.REGISTER && l != Labels.REMINDER && l != Labels.WAITLIST
    }

    private fun rangeCount(node: Node): Int =
        node.descendants().count { n -> n.label?.let { RANGE.containsMatchIn(it) } == true }

    private fun hasChrome(node: Node): Boolean = node.descendants().any { normalize(it.label) in CHROME }

    /**
     * Cards print dd/MM with no year. Take the year that puts the date nearest to
     * today: 03/01 seen on 28/12 is next January, 20/12 seen on 03/01 is last December.
     */
    fun inferYear(day: Int, month: Int, today: LocalDate): LocalDate? {
        val candidates = (today.year - 1..today.year + 1).mapNotNull { y ->
            runCatching { LocalDate.of(y, month, day) }.getOrNull()
        }
        return candidates.minByOrNull { Math.abs(Duration.between(today.atStartOfDay(), it.atStartOfDay()).toDays()) }
    }

    /** Hebrew gershayim (U+05F4) and an ASCII quote are both used for "לו״ז". */
    fun normalize(label: String?): String? = label?.trim()?.replace('״', '"')
}

/**
 * Brings the bookings in line with what the favourites tab shows.
 *
 * The favourites are the only source of what to book. A full read (the sync
 * button, or the one after every run) may remove: a pending class no longer in
 * the favourites was un-hearted. A passive read - whatever the user happens to be
 * looking at - may only add and update, because what is off screen is unknown,
 * not gone.
 */
object Reconciler {
    data class Outcome(
        val bookings: List<Booking>,
        val added: List<Booking>,
        val updated: List<Booking>,
        val removed: List<Booking>,
    )

    fun reconcile(current: List<Booking>, seen: List<FavoriteCard>, complete: Boolean, now: ZonedDateTime): Outcome {
        val byKey = current.associateBy { it.key }.toMutableMap()
        val added = mutableListOf<Booking>()
        val updated = mutableListOf<Booking>()
        val seenKeys = seen.map { it.key }.toSet()

        for (card in seen) {
            val classStart = ZonedDateTime.of(card.date, card.start, Schedule.ISRAEL)
            if (!classStart.isAfter(now)) continue // already started; nothing to book
            val existing = byKey[card.key]

            if (card.action == null) {
                // Not one of the three known actions: most likely registered already.
                if (existing == null || existing.status == BookingStatus.PENDING) {
                    val skipped = (existing ?: newBooking(card, now)).copy(
                        status = BookingStatus.SKIPPED,
                        detail = "the card shows '${card.actionText ?: "nothing"}'",
                    )
                    byKey[card.key] = skipped
                    if (existing == null) added += skipped else updated += skipped
                }
                continue
            }

            if (existing == null) {
                val booking = newBooking(card, now)
                byKey[card.key] = booking
                added += booking
            } else if (existing.status == BookingStatus.PENDING && card.opensAt != null && card.opensAt != existing.opensAt) {
                val moved = existing.copy(opensAt = card.opensAt, opensAtFromCard = true)
                byKey[card.key] = moved
                updated += moved
            }
        }

        val removed = mutableListOf<Booking>()
        if (complete) {
            for (b in byKey.values.toList()) {
                if (b.status == BookingStatus.PENDING && b.key !in seenKeys) {
                    byKey.remove(b.key)
                    removed += b
                }
            }
        }
        // Forget anything whose class ended more than a day ago.
        for (b in byKey.values.toList()) {
            if (b.classStart.isBefore(now.minusDays(1))) byKey.remove(b.key)
        }
        return Outcome(byKey.values.sortedBy { it.classStart }, added, updated, removed)
    }

    private fun newBooking(card: FavoriteCard, now: ZonedDateTime): Booking {
        val classStart = ZonedDateTime.of(card.date, card.start, Schedule.ISRAEL)
        val opensAt = when {
            card.opensAt != null -> card.opensAt
            // Already open (or full): the window is now.
            card.action == Labels.REGISTER || card.action == Labels.WAITLIST -> now
            // Closed but no time shown: the rule every observed class followed.
            else -> classStart.minusHours(Schedule.DEFAULT_LEAD_HOURS)
        }
        return Booking(card.className, card.date, card.start, opensAt, opensAtFromCard = card.opensAt != null)
    }
}

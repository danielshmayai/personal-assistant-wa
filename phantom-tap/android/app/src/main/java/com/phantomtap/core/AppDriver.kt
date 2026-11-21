package com.phantomtap.core

import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime

/** How a run ended. */
enum class Outcome {
    /** Registered; [DriveResult.seat] says which seat, if the class has seats. */
    BOOKED,

    /** The class was full; joined the waitlist ("הרשמה להמתנה"). */
    WAITLISTED,

    /** The class was full and joining the waitlist is switched off. */
    FULL,

    /** Registered for the class, but no acceptable seat was free - see detail. */
    SEAT_UNAVAILABLE,

    /** The card still offered "תזכורת" when the wait ran out. */
    NOT_OPENED,

    /** The app, the favourites tab or the class could not be found. */
    NOT_FOUND,

    /** A rehearsal reached the class card and stopped, as intended. */
    REHEARSED,

    /** Stopped from outside: the stop button, the kill switch, or the runaway guard. */
    STOPPED,

    FAILED,
}

data class DriveResult(
    val outcome: Outcome,
    val seat: Int? = null,
    val detail: String = "",
    val steps: List<String> = emptyList(),
)

/** Everything the driver needs about one window, in plain values. */
data class DrivePlan(
    val className: String,
    val date: LocalDate,
    val start: LocalTime,
    val opensAtMs: Long,
    val giveUpAtMs: Long,
    val preferredSeats: List<Int>,
    val fallback: SeatFallback,
    val joinWaitlist: Boolean,
) {
    val key: String get() = bookingKey(className, date, start)
    val label: String get() = "$className %02d/%02d %02d:%02d".format(date.dayOfMonth, date.monthValue, start.hour, start.minute)

    companion object {
        fun from(booking: Booking, prefs: SeatPrefs) = DrivePlan(
            className = booking.className,
            date = booking.date,
            start = booking.start,
            opensAtMs = booking.opensAt.toInstant().toEpochMilli(),
            giveUpAtMs = booking.giveUpAt.toInstant().toEpochMilli(),
            preferredSeats = prefs.preferredSeats,
            fallback = prefs.fallback,
            joinWaitlist = prefs.joinWaitlist,
        )
    }
}

/** What a full read of the favourites tab found. */
data class SyncResult(
    val cards: List<FavoriteCard>,
    /**
     * True only when the whole list was read, top to bottom. Only a complete read
     * may conclude that a class was un-hearted; a partial one proves nothing.
     */
    val complete: Boolean,
    val detail: String,
    val steps: List<String>,
)

/**
 * Books one class by operating the Holmes Place app, the way a person would.
 *
 *   open the app -> "שיעורי סטודיו" -> "מועדפים" -> the class card
 *   -> wait while the card says "תזכורת"
 *   -> the moment it says "הרשמה", tap it
 *   -> "בחירת מקום": first red (free) seat in priority order -> "המשך"
 *
 * Everything is found by its visible Hebrew label, so nothing depends on the
 * app's internal structure. Every step is recorded, because when this runs at
 * 14:10 nobody is watching, and the steps are how a miss gets explained.
 */
class AppDriver(
    private val screen: Screen,
    private val time: TimeSource,
    private val log: (String) -> Unit = {},
) {
    private val steps = mutableListOf<String>()

    fun run(plan: DrivePlan, rehearsal: Boolean): DriveResult {
        steps.clear()
        return try {
            (time as? StoppableTime)?.check()
            drive(plan, rehearsal)
        } catch (e: Stopped) {
            result(Outcome.STOPPED, detail = e.message ?: "stopped")
        } catch (e: Exception) {
            result(Outcome.FAILED, detail = "${e.javaClass.simpleName}: ${e.message}")
        }
    }

    /**
     * Read every card in the favourites tab, top to bottom.
     *
     * Used by the sync button and after every booking run. Touches no button:
     * it only scrolls and reads.
     */
    fun readFavorites(): SyncResult {
        steps.clear()
        return try {
            (time as? StoppableTime)?.check()
            sync()
        } catch (e: Stopped) {
            step("sync stopped: ${e.message}")
            SyncResult(emptyList(), complete = false, detail = "stopped: ${e.message}", steps = steps.toList())
        } catch (e: Exception) {
            step("sync failed: ${e.javaClass.simpleName}: ${e.message}")
            SyncResult(emptyList(), complete = false, detail = "${e.javaClass.simpleName}: ${e.message}", steps = steps.toList())
        }
    }

    private fun sync(): SyncResult {
        fun stop(detail: String, cards: Collection<FavoriteCard> = emptyList(), complete: Boolean = false): SyncResult {
            step("sync: $detail")
            return SyncResult(cards.toList(), complete, detail, steps.toList())
        }
        if (!openApp()) return stop("the Holmes Place app did not come to the front")
        if (!openFavorites()) return stop("no '${Labels.SUBTAB_FAVORITES}' tab")

        val seen = linkedMapOf<String, FavoriteCard>()
        screen.snapshot()?.let(::classList)?.let {
            scrollToStart(it)
            time.sleep(SETTLE_MS)
        }
        repeat(MAX_LIST_SCROLLS) {
            val root = screen.snapshot() ?: return stop("the screen could not be read", seen.values)
            FavoritesParser.cards(root, today()).forEach { (card, _) -> seen.putIfAbsent(card.key, card) }
            val list = classList(root)
            if (list == null || !screen.scroll(list, forward = true)) {
                // An empty read is never trusted as "nothing is a favourite any
                // more": a list that failed to load looks exactly the same.
                return stop("read ${seen.size} favourite classes", seen.values, complete = seen.isNotEmpty())
            }
            time.sleep(SETTLE_MS)
        }
        return stop("stopped after $MAX_LIST_SCROLLS pages; read ${seen.size}", seen.values)
    }

    private fun drive(plan: DrivePlan, rehearsal: Boolean): DriveResult {
        if (!openApp()) return result(Outcome.FAILED, detail = "the Holmes Place app did not come to the front")
        if (!openFavorites()) return result(Outcome.NOT_FOUND, detail = "no '${Labels.SUBTAB_FAVORITES}' tab")

        val card = findCard(plan, fromTop = true)
            ?: return result(Outcome.NOT_FOUND, detail = "${plan.label} is not in the favourites")
        step("card found, it offers '${actionOf(card) ?: "nothing"}'")

        // A rehearsal proves the whole path up to the card and touches nothing.
        if (rehearsal) return result(Outcome.REHEARSED, detail = "card reached; it offers '${actionOf(card) ?: "nothing"}'")

        return when (waitAndTap(plan)) {
            Tapped.REGISTER -> afterRegister(plan)
            Tapped.WAITLIST -> result(Outcome.WAITLISTED, detail = "class full; joined the waitlist")
            Tapped.FULL -> result(Outcome.FULL, detail = "class full; joining the waitlist is off")
            Tapped.TIMEOUT -> result(Outcome.NOT_OPENED, detail = "still '${Labels.REMINDER}' when the wait ran out")
            Tapped.LOST -> result(Outcome.NOT_FOUND, detail = "the card disappeared from the favourites")
        }
    }

    // ------------------------------------------------------------- navigation --

    private fun openApp(): Boolean {
        if (screen.foregroundPackage() == Labels.PACKAGE) return true.also { step("app already open") }
        if (!screen.launchApp()) return false
        val opened = waitFor(APP_START_MS) { if (screen.foregroundPackage() == Labels.PACKAGE) true else null } == true
        if (opened) step("app opened")
        return opened
    }

    /**
     * "שיעורי סטודיו" -> "מועדפים". The full timetable is never used: the
     * favourites are the only source, and each favourites card carries its own
     * date, so there is no day strip to work through.
     */
    private fun openFavorites(): Boolean {
        val tab = waitFor(NAV_MS) { root -> root.find { it.label == Labels.TAB_STUDIO } } ?: return false
        screen.click(tab)
        step("opened '${Labels.TAB_STUDIO}'")
        val favorites = waitFor(NAV_MS) { root ->
            root.find { FavoritesParser.normalize(it.label) == Labels.SUBTAB_FAVORITES }
        } ?: return false
        screen.click(favorites)
        time.sleep(SETTLE_MS)
        step("opened '${Labels.SUBTAB_FAVORITES}'")
        return true
    }

    /**
     * The card for this class, or null.
     *
     * Matched on name, date and start time together, read off each card. The same
     * weekly class appears in the favourites once per date, so the date matters;
     * two classes at 08:30 are told apart by name; and a class *ending* at 08:30 is
     * not mistaken for one starting then, because the start is read off the range.
     */
    private fun findCard(plan: DrivePlan, fromTop: Boolean): Node? {
        var root = screen.snapshot() ?: return null
        cardIn(root, plan)?.let { return it }

        val list = classList(root) ?: return null
        if (fromTop) {
            scrollToStart(list)
            time.sleep(SETTLE_MS)
            root = screen.snapshot() ?: return null
            cardIn(root, plan)?.let { return it }
        }
        repeat(MAX_LIST_SCROLLS) {
            val current = classList(root) ?: return null
            if (!screen.scroll(current, forward = true)) return null
            time.sleep(SETTLE_MS)
            root = screen.snapshot() ?: return null
            cardIn(root, plan)?.let { return it }
        }
        return null
    }

    private fun cardIn(root: Node, plan: DrivePlan): Node? =
        FavoritesParser.cards(root, today()).firstOrNull { (card, _) -> card.key == plan.key }?.second

    private fun today(): LocalDate = Instant.ofEpochMilli(time.now()).atZone(Schedule.ISRAEL).toLocalDate()

    private fun classList(root: Node): Node? =
        root.descendants().filter { it.scrollable }.maxByOrNull { it.bounds.height }

    // --------------------------------------------------------- the moment ------

    private enum class Tapped { REGISTER, WAITLIST, FULL, TIMEOUT, LOST }

    /**
     * Watch the card until it offers something, and tap it at once.
     *
     * The user reports the button turns into "הרשמה" by itself at the published
     * minute, so the first two seconds past T0 are spent just watching. Only if
     * it has not flipped by then does the driver start refreshing - a pull from
     * the top of the list, then finding the card again - because a refresh moves
     * the list and costs a second or two each time.
     *
     * "תזכורת" is never tapped: it sets a reminder, it does not register.
     */
    private fun waitAndTap(plan: DrivePlan): Tapped {
        var lastRefresh = 0L
        var lastSeen = time.now()
        while (true) {
            val now = time.now()
            val card = screen.snapshot()?.let { cardIn(it, plan) }
                ?: findCard(plan, fromTop = true)

            if (card == null) {
                // A list reloading after a refresh is briefly empty; only a card
                // missing for several seconds is really gone.
                if (now - lastSeen >= LOST_AFTER_MS) return Tapped.LOST
            } else {
                lastSeen = now
                when (actionOf(card)) {
                    Labels.REGISTER -> {
                        card.find { it.label == Labels.REGISTER }?.let(screen::click)
                        step("tapped '${Labels.REGISTER}' at T0${signedMs(time.now() - plan.opensAtMs)}")
                        return Tapped.REGISTER
                    }
                    Labels.WAITLIST -> {
                        if (!plan.joinWaitlist) return Tapped.FULL
                        card.find { it.label == Labels.WAITLIST }?.let(screen::click)
                        step("tapped '${Labels.WAITLIST}'")
                        return Tapped.WAITLIST
                    }
                }
            }

            if (now >= plan.giveUpAtMs) return Tapped.TIMEOUT

            val sinceOpen = now - plan.opensAtMs
            if (sinceOpen >= WATCH_ONLY_MS && now - lastRefresh >= REFRESH_EVERY_MS) {
                refresh()
                lastRefresh = time.now()
            }
            // Relaxed while far from T0, tight around it.
            time.sleep(if (sinceOpen < -5_000) IDLE_POLL_MS else POLL_MS)
        }
    }

    private fun refresh() {
        val root = screen.snapshot() ?: return
        val list = classList(root) ?: return
        // Pull-to-refresh only works with the list at its top.
        scrollToStart(list)
        screen.pullToRefresh(list.bounds)
        time.sleep(SETTLE_MS)
        step("refreshed the favourites")
    }

    // ------------------------------------------------------------------ seats --

    private fun afterRegister(plan: DrivePlan): DriveResult {
        val dialog = waitFor(DIALOG_MS) { root -> root.find { it.label?.contains(Labels.SEAT_DIALOG) == true } }
        if (dialog == null) {
            return result(Outcome.BOOKED, detail = "registered; this class did not ask for a seat")
        }
        step("seat dialog open")

        for (seat in plan.preferredSeats) {
            if (trySeat(seat)) return result(Outcome.BOOKED, seat = seat)
        }
        val fallback = SeatPlan.fallbackOrder(plan.preferredSeats, freeSeats(), plan.fallback)
        for (seat in fallback) {
            if (trySeat(seat)) return result(Outcome.BOOKED, seat = seat, detail = "preferred seats were taken")
        }
        // Whether the app keeps a registration whose seat was never confirmed is
        // not known. Say so plainly rather than report a booking that may not exist.
        return result(
            Outcome.SEAT_UNAVAILABLE,
            detail = "no acceptable seat was free (fallback ${plan.fallback}); check the app - " +
                "the registration may not have completed",
        )
    }

    /**
     * Claim one seat. Taken seats stay listed (gray), so being visible proves
     * nothing; "המשך" turning tappable after the tap is what proves the seat was
     * free, whatever styling the app uses for taken ones.
     */
    private fun trySeat(seat: Int): Boolean {
        val node = locateSeat(seat) ?: return false
        if (!node.enabled) {
            step("seat $seat is taken")
            return false
        }
        screen.click(node)
        val confirm = waitFor(CONFIRM_MS) { root -> root.find { it.label == Labels.CONTINUE && it.enabled } }
        if (confirm == null) {
            step("seat $seat looked free but was not")
            return false
        }
        screen.click(confirm)
        val closed = waitFor(DIALOG_MS) { root ->
            if (root.find { it.label?.contains(Labels.SEAT_DIALOG) == true } == null) true else null
        }
        if (closed == null) {
            step("seat $seat was refused on confirm")
            return false
        }
        step("seat $seat confirmed")
        return true
    }

    private fun seatList(root: Node): Node? = root.descendants()
        .filter { n -> n.scrollable && n.descendants().any { isSeat(it.label) } }
        .maxByOrNull { it.bounds.height }

    /** Find a seat's row, scrolling the list towards it if it is off screen. */
    private fun locateSeat(seat: Int): Node? {
        val label = seat.toString()
        repeat(MAX_LIST_SCROLLS) {
            val root = screen.snapshot() ?: return null
            val list = seatList(root)
            val scope = list ?: root
            scope.find { it.label == label }?.let { return it }
            list ?: return null
            val visible = list.descendants().mapNotNull { it.label?.toIntOrNull() }.toList()
            if (visible.isEmpty()) return null
            val forward = seat > visible.max()
            if (!screen.scroll(list, forward)) return null
            time.sleep(SETTLE_MS)
        }
        return null
    }

    /** Every free seat in the dialog, read by scrolling the whole list once. */
    private fun freeSeats(): Set<Int> {
        val free = sortedSetOf<Int>()
        var root = screen.snapshot() ?: return free
        var list = seatList(root) ?: return root.descendants()
            .filter { isSeat(it.label) && it.enabled }.mapNotNull { it.label?.toIntOrNull() }.toSortedSet()
        scrollToStart(list)
        repeat(MAX_LIST_SCROLLS) {
            root = screen.snapshot() ?: return free
            list = seatList(root) ?: return free
            list.descendants().filter { isSeat(it.label) && it.enabled }
                .mapNotNullTo(free) { it.label?.toIntOrNull() }
            if (!screen.scroll(list, forward = true)) return free
            time.sleep(SETTLE_MS)
        }
        return free
    }

    // ---------------------------------------------------------------- helpers --

    /** Which action a card offers. Exact matches: "הרשמה" is a prefix of "הרשמה להמתנה". */
    private fun actionOf(card: Node): String? = when {
        card.hasExact(Labels.WAITLIST) -> Labels.WAITLIST
        card.hasExact(Labels.REGISTER) -> Labels.REGISTER
        card.hasExact(Labels.REMINDER) -> Labels.REMINDER
        else -> null
    }

    /** Scroll a list back to its start, stopping as soon as it will go no further. */
    private fun scrollToStart(list: Node) {
        var scrolls = 0
        while (scrolls++ < MAX_LIST_SCROLLS && screen.scroll(list, forward = false)) Unit
    }

    private fun <T : Any> waitFor(timeoutMs: Long, probe: (Node) -> T?): T? {
        val deadline = time.now() + timeoutMs
        while (true) {
            screen.snapshot()?.let(probe)?.let { return it }
            if (time.now() >= deadline) return null
            time.sleep(POLL_MS)
        }
    }

    private fun result(outcome: Outcome, seat: Int? = null, detail: String = ""): DriveResult {
        step("result: $outcome${seat?.let { " seat $it" } ?: ""}${if (detail.isNotEmpty()) " - $detail" else ""}")
        return DriveResult(outcome, seat, detail, steps.toList())
    }

    private fun step(text: String) {
        steps += text
        log(text)
    }

    companion object {
        const val POLL_MS = 80L
        const val IDLE_POLL_MS = 500L
        const val SETTLE_MS = 350L
        const val APP_START_MS = 15_000L
        const val NAV_MS = 8_000L
        const val LOST_AFTER_MS = 8_000L
        const val DIALOG_MS = 6_000L
        const val CONFIRM_MS = 1_500L
        /** Watch without refreshing this long past T0 - the button usually flips by itself. */
        const val WATCH_ONLY_MS = 2_000L
        const val REFRESH_EVERY_MS = 1_500L
        const val MAX_LIST_SCROLLS = 15

        private val SEAT = Regex("^\\d{1,3}$")

        fun isSeat(label: String?): Boolean = label != null && SEAT.matches(label)

        private fun signedMs(ms: Long): String = if (ms >= 0) "+${ms}ms" else "${ms}ms"
    }
}

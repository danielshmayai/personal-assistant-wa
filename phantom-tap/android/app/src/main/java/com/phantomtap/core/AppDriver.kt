package com.phantomtap.core

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

    /** The app, the timetable, the day or the class could not be found. */
    NOT_FOUND,

    /** A rehearsal reached the class card and stopped, as intended. */
    REHEARSED,

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
    val startLabel: String,
    val dayLabel: String,
    val opensAtMs: Long,
    val giveUpAtMs: Long,
    val preferredSeats: List<Int>,
    val fallback: SeatFallback,
    val joinWaitlist: Boolean,
) {
    companion object {
        fun from(race: Race) = DrivePlan(
            className = race.watch.className,
            startLabel = race.watch.startLabel,
            dayLabel = race.dayLabel,
            opensAtMs = race.opensAt.toInstant().toEpochMilli(),
            giveUpAtMs = race.giveUpAt.toInstant().toEpochMilli(),
            preferredSeats = race.watch.preferredSeats,
            fallback = race.watch.seatFallback,
            joinWaitlist = race.watch.joinWaitlist,
        )
    }
}

/**
 * Books one class by operating the Holmes Place app, the way a person would.
 *
 *   open the app -> "שיעורי סטודיו" -> the day -> the class card
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
            drive(plan, rehearsal)
        } catch (e: Exception) {
            result(Outcome.FAILED, detail = "${e.javaClass.simpleName}: ${e.message}")
        }
    }

    private fun drive(plan: DrivePlan, rehearsal: Boolean): DriveResult {
        if (!openApp()) return result(Outcome.FAILED, detail = "the Holmes Place app did not come to the front")
        if (!openTimetable()) return result(Outcome.NOT_FOUND, detail = "no '${Labels.TAB_STUDIO}' tab")
        if (!pickDay(plan.dayLabel)) return result(Outcome.NOT_FOUND, detail = "day ${plan.dayLabel} is not in the day strip")

        val card = findCard(plan, fromTop = true)
            ?: return result(Outcome.NOT_FOUND, detail = "${plan.className} ${plan.startLabel} is not on ${plan.dayLabel}")
        step("card found, it offers '${actionOf(card) ?: "nothing"}'")

        // A rehearsal proves the whole path up to the card and touches nothing.
        if (rehearsal) return result(Outcome.REHEARSED, detail = "card reached; it offers '${actionOf(card) ?: "nothing"}'")

        return when (waitAndTap(plan)) {
            Tapped.REGISTER -> afterRegister(plan)
            Tapped.WAITLIST -> result(Outcome.WAITLISTED, detail = "class full; joined the waitlist")
            Tapped.FULL -> result(Outcome.FULL, detail = "class full; joining the waitlist is off")
            Tapped.TIMEOUT -> result(Outcome.NOT_OPENED, detail = "still '${Labels.REMINDER}' when the wait ran out")
            Tapped.LOST -> result(Outcome.NOT_FOUND, detail = "the card disappeared from the timetable")
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

    private fun openTimetable(): Boolean {
        val tab = waitFor(NAV_MS) { root -> root.find { it.label == Labels.TAB_STUDIO } } ?: return false
        screen.click(tab)
        step("opened '${Labels.TAB_STUDIO}'")
        // Make sure the timetable sub-tab is selected, not favourites or bookings.
        waitFor(SUBTAB_MS) { root -> root.find { normalize(it.label) == normalize(Labels.SUBTAB_SCHEDULE) } }
            ?.let(screen::click)
        return waitFor(NAV_MS) { root -> root.find { isDayChip(it.label) } } != null
    }

    private fun pickDay(dayLabel: String): Boolean {
        repeat(MAX_STRIP_SCROLLS) {
            val root = screen.snapshot() ?: return@repeat time.sleep(POLL_MS)
            val chip = root.find { node -> node.label?.split(WHITESPACE)?.contains(dayLabel) == true }
            if (chip != null) {
                screen.click(chip)
                time.sleep(SETTLE_MS)
                step("picked day $dayLabel")
                return true
            }
            // The strip runs right to left; "forward" is towards later days either way.
            val strip = root.find { n -> n.scrollable && n.descendants().any { isDayChip(it.label) } } ?: return false
            if (!screen.scroll(strip, forward = true)) return false
            time.sleep(SETTLE_MS)
        }
        return false
    }

    /**
     * The card for this class, or null.
     *
     * A card is the smallest region that holds the class name, a time range
     * starting at the class start, and one of the three actions. Two classes at
     * 08:30 are told apart by name; a class *ending* at 08:30 is not mistaken for
     * one starting then, because the start is read off the range.
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

    private fun cardIn(root: Node, plan: DrivePlan): Node? {
        val wanted = plan.className.trim()
        val names = root.descendants().filter { it.label?.contains(wanted, ignoreCase = true) == true }
        for (name in names) {
            // The card is the *first* region up from the name that holds an action.
            // Climbing further would reach the list, which holds every card's time
            // and button - and would let a 07:40-08:30 class pass for one at 08:30.
            val card = (sequenceOf(name) + name.ancestors()).take(MAX_CARD_DEPTH)
                .firstOrNull { actionOf(it) != null } ?: continue
            // More than one action means this is a container, not a card: the name
            // was in something like a promo banner.
            if (actionCount(card) == 1 && startsAt(card.subtreeText(), plan.startLabel)) return card
        }
        return null
    }

    private fun actionCount(region: Node): Int = region.descendants().count {
        it.label == Labels.REGISTER || it.label == Labels.REMINDER || it.label == Labels.WAITLIST
    }

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
        step("refreshed the timetable")
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
        const val SUBTAB_MS = 3_000L
        const val LOST_AFTER_MS = 8_000L
        const val DIALOG_MS = 6_000L
        const val CONFIRM_MS = 1_500L
        /** Watch without refreshing this long past T0 - the button usually flips by itself. */
        const val WATCH_ONLY_MS = 2_000L
        const val REFRESH_EVERY_MS = 1_500L
        const val MAX_STRIP_SCROLLS = 8
        const val MAX_LIST_SCROLLS = 15
        const val MAX_CARD_DEPTH = 8

        private val WHITESPACE = Regex("\\s+")
        private val DAY_CHIP = Regex("^\\d{1,2}\\.\\d{1,2}$")
        private val SEAT = Regex("^\\d{1,3}$")
        private val RANGE = Regex("(\\d{1,2}:\\d{2})\\s*[-–]\\s*(\\d{1,2}:\\d{2})")

        /** Hebrew gershayim (U+05F4) and an ASCII quote are both used for "לו״ז". */
        fun normalize(label: String?): String? = label?.replace('\u05F4', '"')

        fun isDayChip(label: String?): Boolean = label?.split(WHITESPACE)?.any { DAY_CHIP.matches(it) } == true

        fun isSeat(label: String?): Boolean = label != null && SEAT.matches(label)

        /**
         * Does this region's text show a class starting at [start]? Reads the
         * start off "08:30 - 09:20" whichever way round right-to-left text puts
         * it, so a class ending at 08:30 does not count.
         */
        fun startsAt(text: String, start: String): Boolean {
            val ranges = RANGE.findAll(text).toList()
            if (ranges.isEmpty()) return text.contains(start)
            return ranges.any { m ->
                val (a, b) = m.destructured
                minOf(a.padStart(5, '0'), b.padStart(5, '0')) == start
            }
        }

        private fun signedMs(ms: Long): String = if (ms >= 0) "+${ms}ms" else "${ms}ms"
    }
}

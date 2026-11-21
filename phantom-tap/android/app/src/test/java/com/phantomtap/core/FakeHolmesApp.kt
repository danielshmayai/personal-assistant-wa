package com.phantomtap.core

/** Time that only moves when the code under test sleeps - minutes pass in microseconds. */
class VirtualTime(var nowMs: Long) : TimeSource {
    override fun now(): Long = nowMs
    override fun sleep(ms: Long) {
        if (ms > 0) nowMs += ms
    }
}

enum class SeatState { FREE, TAKEN, TAKEN_BUT_TAPPABLE }

class FakeClass(
    val id: String,
    val name: String,
    /** As the card prints it: dd/MM. */
    val date: String,
    val dayName: String,
    val start: String,
    val end: String,
    val opensAtMs: Long,
    var full: Boolean = false,
    var favorite: Boolean = true,
    val seats: MutableMap<Int, SeatState> = (1..48).associateWith { SeatState.FREE }.toMutableMap(),
) {
    var registered = false
    var waitlisted = false
    var mySeat: Int? = null
}

/**
 * The Holmes Place app as the screenshots show it, for the driver to operate.
 *
 *  - bottom navigation with "שיעורי סטודיו"
 *  - three sub-tabs: "לו״ז שיעורים" (spelt with the Hebrew gershayim), "מועדפים",
 *    "מוזמנים", the selected one marked as selected
 *  - "מועדפים": every favourite class across dates, two cards per screen. Each
 *    card: "חמישי | 19/11 | 08:30 - 09:25", the name, "instructor / studio", the
 *    capacity numbers, "פתיחת הרשמה: 19/11 03:30" while closed, and the action -
 *    "תזכורת" before the window, "הרשמה" from it, "הרשמה להמתנה" when full,
 *    "ביטול הרשמה" once registered (a label the driver does not know)
 *  - "לו״ז שיעורים": every class, favourite or not - the driver must never use it
 *  - "בחירת מקום": seats 1-48 in a list showing eight rows; taken seats listed but
 *    disabled (gray) or, optionally, tappable but unselectable; "המשך" disabled
 *    until a free seat is selected
 */
class FakeHolmesApp(private val time: VirtualTime) : Screen {
    val classes = mutableListOf<FakeClass>()

    var foreground: String? = "com.sec.android.app.launcher"
    var launchWorks = true
    /** If false, a card only flips after a pull-to-refresh past its opening. */
    var flipsByItself = true
    /** Print the opening line time-first, as right-to-left rendering may. */
    var openingTimeFirst = false
    /** Model an app that does not expose which sub-tab is selected. */
    var exposesSelection = true

    var tab = "home"
    var subtab = "schedule"
    val subtabsOpened = mutableListOf<String>()
    var listOffset = 0
    var seatDialog: FakeClass? = null
    var seatOffset = 0
    var selectedSeat: Int? = null
    private val refreshedAfterOpen = mutableSetOf<String>()
    var refreshes = 0
    /** Seats someone else takes the moment we confirm them - a lost race for the seat. */
    val stolenOnConfirm = mutableSetOf<Int>()

    val taps = mutableListOf<String>()
    var registerTappedAtMs: Long? = null

    fun add(vararg cls: FakeClass) {
        classes.addAll(cls)
    }

    private fun visibleList(): List<FakeClass> = when (subtab) {
        "favorites" -> classes.filter { it.favorite }
        "booked" -> classes.filter { it.registered }
        else -> classes
    }.sortedWith(compareBy({ it.date.split("/").reversed().joinToString() }, { it.start }))

    private fun action(c: FakeClass): String = when {
        c.registered -> "ביטול הרשמה"
        c.waitlisted -> "ביטול המתנה"
        time.now() < c.opensAtMs -> Labels.REMINDER
        !flipsByItself && c.id !in refreshedAfterOpen -> Labels.REMINDER
        c.full -> Labels.WAITLIST
        else -> Labels.REGISTER
    }

    private fun openingLine(c: FakeClass): String? {
        if (time.now() >= c.opensAtMs) return null
        val at = java.time.Instant.ofEpochMilli(c.opensAtMs).atZone(Schedule.ISRAEL)
        val d = "%02d/%02d".format(at.dayOfMonth, at.monthValue)
        val t = "%02d:%02d".format(at.hour, at.minute)
        return if (openingTimeFirst) "פתיחת הרשמה: $t $d" else "פתיחת הרשמה: $d $t"
    }

    // ---------------------------------------------------------------- Screen --

    override fun foregroundPackage(): String? = foreground

    override fun launchApp(): Boolean {
        if (!launchWorks) return false
        foreground = Labels.PACKAGE
        tab = "home"
        return true
    }

    override fun snapshot(): Node? {
        if (foreground != Labels.PACKAGE) return Node("launcher")
        seatDialog?.let { return seatDialogTree(it) }
        val nav = Node(
            null,
            children = listOf(
                Node("בית", clickable = true, ref = "tab:home"),
                Node(Labels.TAB_STUDIO, clickable = true, ref = "tab:studio"),
                Node("חנות", clickable = true, ref = "tab:shop"),
            ),
        )
        if (tab != "studio") return Node(null, children = listOf(Node("ברוכים הבאים"), nav))

        fun sub(label: String, key: String) =
            Node(label, clickable = true, selected = exposesSelection && subtab == key, ref = "subtab:$key")
        val subtabs = Node(
            null,
            children = listOf(
                sub("לו״ז שיעורים", "schedule"),
                sub(Labels.SUBTAB_FAVORITES, "favorites"),
                sub(Labels.SUBTAB_BOOKED, "booked"),
            ),
        )
        val cards = visibleList().drop(listOffset).take(2).map { c ->
            Node(
                null,
                children = listOfNotNull(
                    Node("${c.dayName} | ${c.date} | ${c.start} - ${c.end}"),
                    Node(c.name),
                    Node("סיון אריאלי / סטודיו 1"),
                    Node("45"),
                    Node("38"),
                    openingLine(c)?.let { Node(it) },
                    Node(action(c), clickable = true, ref = "action:${c.id}"),
                ),
            )
        }
        val banner = Node("BLACK NOVEMBER - פילאטיס מכשירים 5 אימונים", ref = "banner")
        val listChildren = (if (listOffset == 0) listOf(banner) else emptyList()) + cards
        val list = Node(null, scrollable = true, bounds = Bounds(0, 500, 1080, 2000), children = listChildren, ref = "list")
        return Node(null, children = listOf(Node("הולמס פלייס מודיעין"), subtabs, list, nav))
    }

    private fun seatDialogTree(c: FakeClass): Node {
        val rows = (1..48).drop(seatOffset).take(8).map { n ->
            Node("$n", enabled = c.seats[n] != SeatState.TAKEN, clickable = true, ref = "seat:$n")
        }
        return Node(
            null,
            children = listOf(
                Node("${Labels.SEAT_DIALOG} ${c.name}"),
                Node("X", clickable = true, ref = "close"),
                Node(null, scrollable = true, bounds = Bounds(0, 800, 1080, 2000), children = rows, ref = "seats"),
                Node(Labels.CONTINUE, enabled = selectedSeat != null, clickable = true, ref = "continue"),
            ),
        )
    }

    override fun click(node: Node): Boolean {
        val ref = node.ref as? String ?: return false
        taps += node.label ?: ref
        when {
            ref == "tab:studio" -> tab = "studio"
            ref.startsWith("subtab:") -> {
                subtab = ref.removePrefix("subtab:")
                subtabsOpened += subtab
                listOffset = 0
            }
            ref.startsWith("action:") -> {
                val c = classes.first { it.id == ref.removePrefix("action:") }
                when (node.label) {
                    Labels.REGISTER -> {
                        registerTappedAtMs = time.now()
                        seatDialog = c
                        seatOffset = 0
                        selectedSeat = null
                    }
                    Labels.WAITLIST -> c.waitlisted = true
                    // REMINDER sets a reminder; the tests assert it is never tapped.
                }
            }
            ref.startsWith("seat:") -> {
                val n = ref.removePrefix("seat:").toInt()
                if (seatDialog?.seats?.get(n) == SeatState.FREE) selectedSeat = n
            }
            ref == "continue" -> {
                val c = seatDialog ?: return false
                val seat = selectedSeat ?: return false
                if (seat in stolenOnConfirm) {
                    c.seats[seat] = SeatState.TAKEN
                    selectedSeat = null
                    return true // the dialog stays open: refused
                }
                c.seats[seat] = SeatState.TAKEN
                c.registered = true
                c.mySeat = seat
                seatDialog = null
            }
        }
        return true
    }

    override fun scroll(node: Node, forward: Boolean): Boolean {
        val step = if (forward) 1 else -1
        return when (node.ref) {
            "list" -> moveTo(listOffset, listOffset + step, visibleList().size - 2) { listOffset = it }
            "seats" -> moveTo(seatOffset, seatOffset + 4 * step, 48 - 8) { seatOffset = it }
            else -> false
        }
    }

    /** Clamp to the content and report whether anything actually moved - false at either end. */
    private inline fun moveTo(current: Int, target: Int, max: Int, set: (Int) -> Unit): Boolean {
        val clamped = target.coerceIn(0, maxOf(0, max))
        set(clamped)
        return clamped != current
    }

    override fun pullToRefresh(area: Bounds): Boolean {
        // Only a pull with the list at its top refreshes; otherwise it just scrolls up.
        if (listOffset != 0) {
            listOffset -= 1
            return false
        }
        refreshes++
        classes.filter { time.now() >= it.opensAtMs }.forEach { refreshedAfterOpen += it.id }
        return true
    }
}

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
    val start: String,
    val end: String,
    val opensAtMs: Long,
    var full: Boolean = false,
    val seats: MutableMap<Int, SeatState> = (1..48).associateWith { SeatState.FREE }.toMutableMap(),
) {
    var registered = false
    var waitlisted = false
    var mySeat: Int? = null
}

/**
 * The Holmes Place app as the screenshots show it, for the driver to operate.
 *
 *  - bottom navigation with "שיעורי סטודיו"; a "לו״ז שיעורים" sub-tab (spelt with
 *    the Hebrew gershayim, to prove the driver copes with it)
 *  - a right-to-left strip of day chips, five visible at a time
 *  - a class list, two cards visible at a time, plus a banner that mentions a
 *    class name but is not a card
 *  - each card: "חמישי | 19/11 | 08:30 - 09:20", the name, the instructor, and an
 *    action: "תזכורת" before the window, "הרשמה" from it, "הרשמה להמתנה" if full
 *  - "בחירת מקום": seats 1-48 in a list showing eight rows; taken seats listed but
 *    disabled (gray) or, optionally, tappable but unselectable; "המשך" disabled
 *    until a free seat is selected
 */
class FakeHolmesApp(private val time: VirtualTime) : Screen {
    val days = listOf("19.11", "20.11", "21.11", "22.11", "23.11", "24.11", "25.11", "26.11")
    private val dayNames = listOf("חמישי", "שישי", "שבת", "ראשון", "שני", "שלישי", "רביעי", "חמישי")
    val classes = mutableMapOf<String, MutableList<FakeClass>>()

    var foreground: String? = "com.sec.android.app.launcher"
    var launchWorks = true
    /** If false, the card only flips after a pull-to-refresh past T0. */
    var flipsByItself = true

    var tab = "home"
    var subtabClicked = false
    var stripOffset = 0
    var selectedDay: String? = null
    var listOffset = 0
    var seatDialog: FakeClass? = null
    var seatOffset = 0
    var selectedSeat: Int? = null
    var refreshedAfterOpen = mutableSetOf<String>()
    var refreshes = 0
    /** Seats someone else takes the moment we confirm them - a lost race for the seat. */
    var stolenOnConfirm = mutableSetOf<Int>()

    val taps = mutableListOf<String>()
    var registerTappedAtMs: Long? = null

    fun add(day: String, vararg cls: FakeClass) {
        classes.getOrPut(day) { mutableListOf() }.addAll(cls)
    }

    private fun action(c: FakeClass): String = when {
        c.registered -> "ביטול הרשמה"
        c.waitlisted -> "ביטול המתנה"
        time.now() < c.opensAtMs -> Labels.REMINDER
        !flipsByItself && c.id !in refreshedAfterOpen -> Labels.REMINDER
        c.full -> Labels.WAITLIST
        else -> Labels.REGISTER
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

        val chips = days.indices.drop(stripOffset).take(5).map { i ->
            Node("${dayNames[i]} ${days[i]}", clickable = true, ref = "chip:${days[i]}")
        }
        val strip = Node(null, scrollable = true, bounds = Bounds(0, 300, 1080, 450), children = chips, ref = "strip")

        val todays = classes[selectedDay].orEmpty()
        val banner = Node("BLACK NOVEMBER - פילאטיס מכשירים 5 אימונים", ref = "banner")
        val cards = todays.drop(listOffset).take(2).map { c ->
            Node(
                null,
                children = listOf(
                    Node("חמישי | 19/11 | ${c.start} - ${c.end}"),
                    Node(c.name),
                    Node("סיון אריאלי / סטודיו 1"),
                    Node(action(c), clickable = true, ref = "action:${c.id}"),
                ),
            )
        }
        val listChildren = (if (listOffset == 0) listOf(banner) else emptyList()) + cards
        val list = Node(null, scrollable = true, bounds = Bounds(0, 500, 1080, 2000), children = listChildren, ref = "list")
        val subtabs = Node(
            null,
            children = listOf(
                Node("לו״ז שיעורים", clickable = true, ref = "subtab"),
                Node("מועדפים", clickable = true),
                Node("מוזמנים", clickable = true),
            ),
        )
        return Node(null, children = listOf(Node("הולמס פלייס מודיעין"), subtabs, strip, list, nav))
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
            ref == "subtab" -> subtabClicked = true
            ref.startsWith("chip:") -> {
                selectedDay = ref.removePrefix("chip:")
                listOffset = 0
            }
            ref.startsWith("action:") -> {
                val c = classes.values.flatten().first { it.id == ref.removePrefix("action:") }
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
            "strip" -> moveTo(stripOffset, stripOffset + step, days.size - 5) { stripOffset = it }
            "list" -> moveTo(listOffset, listOffset + step, classes[selectedDay].orEmpty().size - 2) { listOffset = it }
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
        classes[selectedDay].orEmpty().filter { time.now() >= it.opensAtMs }.forEach { refreshedAfterOpen += it.id }
        return true
    }
}

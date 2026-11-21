package com.phantomtap.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZonedDateTime

/**
 * The driver against a fake of the Holmes Place app, in virtual time.
 *
 * Each test arrives two minutes before the window, as the real alarm does, so the
 * whole path - open the app, "מועדפים", find the class, wait, tap, choose a seat -
 * runs end to end every time.
 */
class AppDriverTest {
    private val classDate: LocalDate = LocalDate.of(2026, 11, 19) // a Thursday
    // An 08:30 class opens at 03:30: the published opening minute, T0.
    private val t0 = ZonedDateTime.of(classDate, LocalTime.of(3, 30), Schedule.ISRAEL).toInstant().toEpochMilli()
    private val time = VirtualTime(t0 - Schedule.ARRIVE_EARLY_SECONDS * 1000)
    private val app = FakeHolmesApp(time)

    private fun cls(
        id: String = "bone",
        name: String = "התעמלות בונה עצם",
        date: String = "19/11",
        dayName: String = "חמישי",
        start: String = "08:30",
        end: String = "09:25",
        opensAt: Long = t0,
        full: Boolean = false,
        favorite: Boolean = true,
    ) = FakeClass(id, name, date, dayName, start, end, opensAt, full, favorite)

    private fun plan(
        name: String = "התעמלות בונה עצם",
        date: LocalDate = classDate,
        start: String = "08:30",
        seats: List<Int> = listOf(12, 13),
        fallback: SeatFallback = SeatFallback.NEAREST,
        waitlist: Boolean = true,
    ) = DrivePlan(
        className = name,
        date = date,
        start = LocalTime.parse(start),
        opensAtMs = t0,
        giveUpAtMs = t0 + Schedule.GIVE_UP_AFTER_SECONDS * 1000,
        preferredSeats = seats,
        fallback = fallback,
        joinWaitlist = waitlist,
    )

    private fun run(plan: DrivePlan = plan(), rehearsal: Boolean = false) =
        AppDriver(app, time).run(plan, rehearsal)

    // ------------------------------------------------------------ the race --

    @Test
    fun `taps register the moment it appears and takes the first preferred seat`() {
        val c = cls()
        app.add(c)

        val result = run()

        assertEquals(result.steps.joinToString("\n"), Outcome.BOOKED, result.outcome)
        assertEquals(12, result.seat)
        assertTrue(c.registered)
        val tappedAt = app.registerTappedAtMs
        assertNotNull(tappedAt)
        assertTrue("tapped ${tappedAt!! - t0}ms after T0", tappedAt - t0 in 0..AppDriver.POLL_MS)
    }

    @Test
    fun `never taps the reminder button`() {
        app.add(cls())
        run()
        assertFalse(app.taps.toString(), Labels.REMINDER in app.taps)
    }

    @Test
    fun `a window that is already open is taken at once`() {
        // Opened before the driver even arrived (it arrives at T0 - 120s).
        app.add(cls(opensAt = t0 - 180_000))
        val arrived = time.now()

        val result = run()

        assertEquals(Outcome.BOOKED, result.outcome)
        assertTrue("should not wait for T0 when already open", app.registerTappedAtMs!! < t0)
        assertTrue(app.registerTappedAtMs!! - arrived < 10_000)
    }

    @Test
    fun `refreshes from the top of the list when the card does not flip by itself`() {
        app.flipsByItself = false
        // Two earlier favourites push ours below the fold, so a pull without first
        // scrolling to the top would not refresh anything.
        app.add(
            cls(id = "a", name = "פלדנקרייז", start = "07:15", end = "08:05"),
            cls(id = "b", name = "יוגה", start = "07:30", end = "08:25"),
            cls(),
        )

        val result = run()

        assertEquals(result.steps.joinToString("\n"), Outcome.BOOKED, result.outcome)
        assertTrue(app.refreshes >= 1)
        assertTrue("watched first, refreshed after", app.registerTappedAtMs!! >= t0 + AppDriver.WATCH_ONLY_MS)
    }

    @Test
    fun `gives up when the window never opens and taps nothing`() {
        app.add(cls(opensAt = t0 + 3_600_000))

        val result = run()

        assertEquals(Outcome.NOT_OPENED, result.outcome)
        assertTrue(app.taps.none { it == Labels.REGISTER || it == Labels.REMINDER })
        assertTrue(time.now() >= t0 + Schedule.GIVE_UP_AFTER_SECONDS * 1000)
    }

    // ------------------------------------------------- favourites, not timetable --

    @Test
    fun `goes to the favourites and never opens the full timetable`() {
        app.add(cls())

        run()

        assertEquals(listOf("favorites"), app.subtabsOpened)
    }

    @Test
    fun `a class that is not a favourite is not booked, even though it is on the timetable`() {
        val notFavourite = cls(favorite = false)
        app.add(notFavourite)

        val result = run()

        assertEquals(Outcome.NOT_FOUND, result.outcome)
        assertFalse(notFavourite.registered)
    }

    @Test
    fun `the same weekly class on two dates - the right date is booked`() {
        val thisWeek = cls(id = "w1")
        val nextWeek = cls(id = "w2", date = "26/11")
        app.add(thisWeek, nextWeek)

        val result = run(plan(date = LocalDate.of(2026, 11, 26)))

        assertEquals(result.steps.joinToString("\n"), Outcome.BOOKED, result.outcome)
        assertTrue(nextWeek.registered)
        assertFalse(thisWeek.registered)
    }

    @Test
    fun `two classes at the same time are told apart by name`() {
        // The 19/11 screenshot: פילאטיס מכשירים 08:30 (full) next to התעמלות בונה עצם 08:30.
        val pilates = cls(id = "pilates", name = "פילאטיס מכשירים", end = "09:20", full = true)
        val bone = cls()
        app.add(pilates, bone)

        val result = run()

        assertEquals(Outcome.BOOKED, result.outcome)
        assertTrue(bone.registered)
        assertFalse(pilates.waitlisted)
    }

    @Test
    fun `a class ending at the start time is not mistaken for one starting then`() {
        val earlier = cls(id = "early", name = "HIIT", start = "07:40", end = "08:30")
        val target = cls(id = "target", name = "HIIT", start = "08:30", end = "09:20")
        app.add(earlier, target)

        run(plan(name = "HIIT"))

        assertTrue(target.registered)
        assertFalse(earlier.registered)
    }

    @Test
    fun `a banner mentioning the class is not taken for its card`() {
        app.add(cls(id = "p", name = "פילאטיס מכשירים", end = "09:20"))
        val result = run(plan(name = "פילאטיס מכשירים"))
        assertEquals(Outcome.BOOKED, result.outcome)
    }

    // ----------------------------------------------------------- the seats --

    @Test
    fun `gray seats are skipped without being tapped`() {
        val c = cls()
        c.seats[12] = SeatState.TAKEN
        app.add(c)

        val result = run()

        assertEquals(13, result.seat)
        assertFalse("12" in app.taps)
    }

    @Test
    fun `a taken seat that is still tappable is caught by the continue button`() {
        val c = cls()
        c.seats[12] = SeatState.TAKEN_BUT_TAPPABLE
        app.add(c)

        val result = run()

        assertEquals(13, result.seat)
        assertTrue("12" in app.taps)
    }

    @Test
    fun `falls back to the nearest free seat - the screenshot where only 2 and 4 were red`() {
        val c = cls()
        (1..48).forEach { c.seats[it] = SeatState.TAKEN }
        c.seats[2] = SeatState.FREE
        c.seats[4] = SeatState.FREE
        c.seats[30] = SeatState.FREE
        app.add(c)

        val result = run(plan(seats = listOf(12, 13)))

        assertEquals(Outcome.BOOKED, result.outcome)
        assertEquals(4, result.seat) // |13-4| = 9 beats |12-2| = 10 and |13-30| = 17
    }

    @Test
    fun `a preferred seat far down the list is scrolled to`() {
        app.add(cls())
        assertEquals(40, run(plan(seats = listOf(40))).seat)
    }

    @Test
    fun `a seat lost at confirm moves on to the next preference`() {
        app.stolenOnConfirm += 12
        app.add(cls())
        assertEquals(13, run().seat)
    }

    @Test
    fun `with fallback NONE and every preference taken it says so instead of claiming a booking`() {
        val c = cls()
        c.seats[12] = SeatState.TAKEN
        c.seats[13] = SeatState.TAKEN
        app.add(c)

        val result = run(plan(fallback = SeatFallback.NONE))

        assertEquals(Outcome.SEAT_UNAVAILABLE, result.outcome)
        assertNull(result.seat)
        assertTrue(result.detail.contains("may not have completed"))
    }

    // ---------------------------------------------------------- waitlist --

    @Test
    fun `a full class joins the waitlist when allowed`() {
        val c = cls(full = true)
        app.add(c)

        val result = run()

        assertEquals(Outcome.WAITLISTED, result.outcome)
        assertTrue(c.waitlisted)
    }

    @Test
    fun `a full class is left alone when the waitlist is off`() {
        val c = cls(full = true)
        app.add(c)

        val result = run(plan(waitlist = false))

        assertEquals(Outcome.FULL, result.outcome)
        assertFalse(c.waitlisted)
        assertFalse(Labels.WAITLIST in app.taps)
    }

    // -------------------------------------------------------------- sync --

    @Test
    fun `sync reads every favourite, scrolling, and nothing else`() {
        app.add(
            cls(id = "a", name = "פלדנקרייז", start = "07:15", end = "08:05"),
            cls(id = "b"),
            cls(id = "c", name = "HIIT", date = "20/11", dayName = "שישי", start = "18:30", end = "19:25"),
            cls(id = "d", name = "BODY POWER", date = "25/11", dayName = "רביעי", start = "19:10", end = "20:00"),
            cls(id = "e", name = "ספינינג", start = "20:20", end = "21:05", favorite = false),
        )

        val sync = AppDriver(app, time).readFavorites()

        assertTrue(sync.steps.joinToString("\n"), sync.complete)
        assertEquals(setOf("פלדנקרייז", "התעמלות בונה עצם", "HIIT", "BODY POWER"), sync.cards.map { it.className }.toSet())
        val bodyPower = sync.cards.first { it.className == "BODY POWER" }
        assertEquals(LocalDate.of(2026, 11, 25), bodyPower.date)
        assertEquals(LocalTime.of(19, 10), bodyPower.start)
        assertEquals(Labels.REMINDER, bodyPower.action)
        assertTrue("sync taps nothing but navigation", app.taps.none { it == Labels.REMINDER || it == Labels.REGISTER })
    }

    @Test
    fun `sync reads the opening time off the card, whichever way round it is printed`() {
        app.openingTimeFirst = true
        app.add(cls())

        val card = AppDriver(app, time).readFavorites().cards.single()

        assertEquals(ZonedDateTime.of(classDate, LocalTime.of(3, 30), Schedule.ISRAEL), card.opensAt)
    }

    @Test
    fun `sync sees an already registered class by its unknown button`() {
        val c = cls()
        c.registered = true
        app.add(c)

        val card = AppDriver(app, time).readFavorites().cards.single()

        assertNull(card.action)
        assertEquals("ביטול הרשמה", card.actionText)
    }

    @Test
    fun `an empty favourites read is never trusted as complete`() {
        val sync = AppDriver(app, time).readFavorites()
        assertTrue(sync.cards.isEmpty())
        assertFalse("a list that failed to load looks the same as an empty one", sync.complete)
    }

    // ------------------------------------------------------------ the rest --

    @Test
    fun `a rehearsal reaches the card and touches nothing`() {
        val c = cls()
        app.add(c)

        val result = run(rehearsal = true)

        assertEquals(Outcome.REHEARSED, result.outcome)
        assertTrue(result.detail.contains(Labels.REMINDER))
        assertFalse(c.registered)
        assertTrue(app.taps.none { it == Labels.REGISTER || it == Labels.REMINDER || it == Labels.WAITLIST })
    }

    @Test
    fun `an app that will not open is reported`() {
        app.launchWorks = false
        assertEquals(Outcome.FAILED, run().outcome)
    }

    @Test
    fun `every run explains itself step by step`() {
        app.add(cls())
        val steps = run().steps
        assertTrue(steps.any { it == "opened '${Labels.SUBTAB_FAVORITES}'" })
        assertTrue(steps.any { it.startsWith("tapped '${Labels.REGISTER}' at T0+") })
        assertTrue(steps.any { it == "seat 12 confirmed" })
    }
}

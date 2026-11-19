package com.phantomtap.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The driver against a fake of the Holmes Place app, in virtual time.
 *
 * Each test arrives two minutes before the window, as the real alarm does, so the
 * whole path - open the app, find the class, wait, tap, choose a seat - runs end
 * to end every time.
 */
class AppDriverTest {
    private val t0 = 1_763_554_200_000L // the published opening minute
    private val time = VirtualTime(t0 - Schedule.ARRIVE_EARLY_SECONDS * 1000)
    private val app = FakeHolmesApp(time)

    private fun cls(
        id: String = "bone",
        name: String = "התעמלות בונה עצם",
        start: String = "08:30",
        end: String = "09:25",
        opensAt: Long = t0,
        full: Boolean = false,
    ) = FakeClass(id, name, start, end, opensAt, full)

    private fun plan(
        name: String = "התעמלות בונה עצם",
        start: String = "08:30",
        day: String = "19.11",
        seats: List<Int> = listOf(12, 13),
        fallback: SeatFallback = SeatFallback.NEAREST,
        waitlist: Boolean = true,
    ) = DrivePlan(
        className = name,
        startLabel = start,
        dayLabel = day,
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
        app.add("19.11", c)

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
        app.add("19.11", cls())
        run()
        assertFalse(app.taps.toString(), Labels.REMINDER in app.taps)
    }

    @Test
    fun `a window that is already open is taken at once`() {
        // Opened before the driver even arrived (it arrives at T0 - 120s).
        app.add("19.11", cls(opensAt = t0 - 180_000))
        val arrived = time.now()

        val result = run()

        assertEquals(Outcome.BOOKED, result.outcome)
        assertTrue(app.registerTappedAtMs!! < t0, "should not wait for T0 when already open")
        assertTrue(app.registerTappedAtMs!! - arrived < 10_000)
    }

    @Test
    fun `refreshes from the top of the list when the card does not flip by itself`() {
        app.flipsByItself = false
        // Two earlier classes push ours below the fold, so a pull without first
        // scrolling to the top would not refresh anything.
        app.add(
            "19.11",
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
        app.add("19.11", cls(opensAt = t0 + 3_600_000))

        val result = run()

        assertEquals(Outcome.NOT_OPENED, result.outcome)
        assertTrue(app.taps.none { it == Labels.REGISTER || it == Labels.REMINDER })
        assertTrue(time.now() >= t0 + Schedule.GIVE_UP_AFTER_SECONDS * 1000)
    }

    // ------------------------------------------------------------- the card --

    @Test
    fun `two classes at the same time are told apart by name`() {
        // The 19/11 screenshot: פילאטיס מכשירים 08:30 (full) above התעמלות בונה עצם 08:30.
        val pilates = cls(id = "pilates", name = "פילאטיס מכשירים", end = "09:20", full = true)
        val bone = cls()
        app.add("19.11", pilates, bone)

        val result = run()

        assertEquals(Outcome.BOOKED, result.outcome)
        assertTrue(bone.registered)
        assertFalse(pilates.waitlisted)
    }

    @Test
    fun `a class ending at the start time is not mistaken for one starting then`() {
        val earlier = cls(id = "early", name = "HIIT", start = "07:40", end = "08:30")
        val target = cls(id = "target", name = "HIIT", start = "08:30", end = "09:20")
        app.add("19.11", earlier, target)

        run(plan(name = "HIIT"))

        assertTrue(target.registered)
        assertFalse(earlier.registered)
    }

    @Test
    fun `a banner mentioning the class is not taken for its card`() {
        app.add("19.11", cls(id = "p", name = "פילאטיס מכשירים", end = "09:20"))
        val result = run(plan(name = "פילאטיס מכשירים"))
        assertEquals(Outcome.BOOKED, result.outcome)
    }

    @Test
    fun `scrolls the day strip to reach a later day`() {
        app.add("25.11", cls()) // sixth chip; only five are visible

        val result = run(plan(day = "25.11"))

        assertEquals(result.steps.joinToString("\n"), Outcome.BOOKED, result.outcome)
        assertEquals("25.11", app.selectedDay)
    }

    @Test
    fun `selects the timetable sub-tab even when spelt with gershayim`() {
        app.add("19.11", cls())
        run()
        assertTrue(app.subtabClicked)
    }

    // ----------------------------------------------------------- the seats --

    @Test
    fun `gray seats are skipped without being tapped`() {
        val c = cls()
        c.seats[12] = SeatState.TAKEN
        app.add("19.11", c)

        val result = run()

        assertEquals(13, result.seat)
        assertFalse("12" in app.taps)
    }

    @Test
    fun `a taken seat that is still tappable is caught by the continue button`() {
        val c = cls()
        c.seats[12] = SeatState.TAKEN_BUT_TAPPABLE
        app.add("19.11", c)

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
        app.add("19.11", c)

        val result = run(plan(seats = listOf(12, 13)))

        assertEquals(Outcome.BOOKED, result.outcome)
        assertEquals(4, result.seat) // |13-4| = 9 beats |12-2| = 10 and |13-30| = 17
    }

    @Test
    fun `a preferred seat far down the list is scrolled to`() {
        val c = cls()
        app.add("19.11", c)

        val result = run(plan(seats = listOf(40)))

        assertEquals(40, result.seat)
    }

    @Test
    fun `a seat lost at confirm moves on to the next preference`() {
        app.stolenOnConfirm += 12
        val c = cls()
        app.add("19.11", c)

        val result = run()

        assertEquals(13, result.seat)
    }

    @Test
    fun `with fallback NONE and every preference taken it says so instead of claiming a booking`() {
        val c = cls()
        c.seats[12] = SeatState.TAKEN
        c.seats[13] = SeatState.TAKEN
        app.add("19.11", c)

        val result = run(plan(fallback = SeatFallback.NONE))

        assertEquals(Outcome.SEAT_UNAVAILABLE, result.outcome)
        assertNull(result.seat)
        assertTrue(result.detail.contains("may not have completed"))
    }

    // ---------------------------------------------------------- waitlist --

    @Test
    fun `a full class joins the waitlist when allowed`() {
        val c = cls(full = true)
        app.add("19.11", c)

        val result = run()

        assertEquals(Outcome.WAITLISTED, result.outcome)
        assertTrue(c.waitlisted)
    }

    @Test
    fun `a full class is left alone when the waitlist is off`() {
        val c = cls(full = true)
        app.add("19.11", c)

        val result = run(plan(waitlist = false))

        assertEquals(Outcome.FULL, result.outcome)
        assertFalse(c.waitlisted)
        assertFalse(Labels.WAITLIST in app.taps)
    }

    // ------------------------------------------------------------ the rest --

    @Test
    fun `a rehearsal reaches the card and touches nothing`() {
        val c = cls()
        app.add("19.11", c)

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
    fun `a class that is not on that day is reported`() {
        app.add("19.11", cls(name = "ספינינג"))
        assertEquals(Outcome.NOT_FOUND, run().outcome)
    }

    @Test
    fun `every run explains itself step by step`() {
        app.add("19.11", cls())
        val steps = run().steps
        assertTrue(steps.any { it.startsWith("tapped '${Labels.REGISTER}' at T0+") })
        assertTrue(steps.any { it == "seat 12 confirmed" })
    }

    private fun assertTrue(condition: Boolean, message: String) = assertTrue(message, condition)
}

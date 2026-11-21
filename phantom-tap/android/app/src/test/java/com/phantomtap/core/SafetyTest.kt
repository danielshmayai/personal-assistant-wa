package com.phantomtap.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZonedDateTime

class SafetyTest {
    private val classDate: LocalDate = LocalDate.of(2026, 11, 19)
    private val t0 = ZonedDateTime.of(classDate, LocalTime.of(3, 30), Schedule.ISRAEL).toInstant().toEpochMilli()
    private val clock = VirtualTime(t0 - Schedule.ARRIVE_EARLY_SECONDS * 1000)
    private val app = FakeHolmesApp(clock).apply {
        add(FakeClass("bone", "התעמלות בונה עצם", "19/11", "חמישי", "08:30", "09:25", t0))
    }

    private val plan = DrivePlan(
        className = "התעמלות בונה עצם",
        date = classDate,
        start = LocalTime.of(8, 30),
        opensAtMs = t0,
        giveUpAtMs = t0 + Schedule.GIVE_UP_AFTER_SECONDS * 1000,
        preferredSeats = listOf(12),
        fallback = SeatFallback.NEAREST,
        joinWaitlist = true,
    )

    private fun stoppingAt(atMs: Long) = StoppableTime(clock) { if (clock.now() >= atMs) "עצרת ידנית" else null }

    // ------------------------------------------------------------------- stop --

    @Test
    fun `a stop while waiting for the window ends the run at once and taps nothing`() {
        val stopAt = t0 - 30_000 // half a minute before the window, mid-wait
        val result = AppDriver(app, stoppingAt(stopAt)).run(plan, rehearsal = false)

        assertEquals(Outcome.STOPPED, result.outcome)
        assertEquals("עצרת ידנית", result.detail)
        assertTrue("stopped within one poll", clock.now() - stopAt <= AppDriver.IDLE_POLL_MS)
        assertFalse(app.classes.single().registered)
        assertTrue(app.taps.none { it == Labels.REGISTER })
    }

    @Test
    fun `a stop requested before the run starts never even opens the app`() {
        val result = AppDriver(app, stoppingAt(0)).run(plan, rehearsal = false)

        assertEquals(Outcome.STOPPED, result.outcome)
        assertEquals("com.sec.android.app.launcher", app.foreground)
    }

    @Test
    fun `a stopped sync is never complete, so it can remove nothing`() {
        val sync = AppDriver(app, stoppingAt(0)).readFavorites()

        assertFalse(sync.complete)
        assertTrue(sync.detail.startsWith("stopped"))
    }

    @Test
    fun `without a stop the same run books as usual`() {
        val result = AppDriver(app, stoppingAt(Long.MAX_VALUE)).run(plan, rehearsal = false)
        assertEquals(Outcome.BOOKED, result.outcome)
    }

    // ------------------------------------------------------------------ guard --

    private val hour = 60 * 60 * 1000L

    @Test
    fun `a normal week of sessions passes`() {
        var starts = emptyList<Long>()
        repeat(RunawayGuard.MAX_SESSIONS_PER_HOUR) { i ->
            val d = RunawayGuard.admit(starts, now = i * 60_000L)
            assertTrue(d.allowed)
            starts = d.recentStarts
        }
    }

    @Test
    fun `a burst of sessions in an hour trips the guard`() {
        val starts = (0 until RunawayGuard.MAX_SESSIONS_PER_HOUR).map { it * 1_000L }
        val d = RunawayGuard.admit(starts, now = 10_000)

        assertFalse(d.allowed)
        assertTrue(d.reason!!.contains("${RunawayGuard.MAX_SESSIONS_PER_HOUR + 1} הפעלות"))
    }

    @Test
    fun `sessions older than an hour no longer count`() {
        val old = (0 until RunawayGuard.MAX_SESSIONS_PER_HOUR).map { it * 1_000L }
        val d = RunawayGuard.admit(old, now = hour + 10_000)

        assertTrue(d.allowed)
        assertEquals(listOf(hour + 10_000), d.recentStarts)
    }

    @Test
    fun `the runaway the guard exists for - an alarm re-firing every second`() {
        var starts = emptyList<Long>()
        var stoppedAfter = -1
        for (second in 0 until 210) { // the 3.5 minutes the old re-arm loop could run
            val d = RunawayGuard.admit(starts, now = second * 1_000L)
            if (!d.allowed) {
                stoppedAfter = second
                break
            }
            starts = d.recentStarts
        }
        assertEquals(RunawayGuard.MAX_SESSIONS_PER_HOUR, stoppedAfter)
    }
}

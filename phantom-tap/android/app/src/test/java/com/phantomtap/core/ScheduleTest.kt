package com.phantomtap.core

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZonedDateTime

class ScheduleTest {
    // BODY POWER, Wednesday 19:10 - registration observed opening at 14:10.
    private val bodyPower = Watch("bp", "BODY POWER", DayOfWeek.WEDNESDAY, LocalTime.of(19, 10))

    private fun at(y: Int, m: Int, d: Int, h: Int, min: Int, s: Int = 0) =
        ZonedDateTime.of(y, m, d, h, min, s, 0, Schedule.ISRAEL)

    @Test
    fun `opens five hours before the class, as observed`() {
        val race = Schedule.nextRace(bodyPower, at(2026, 9, 16, 9, 0)) // Wed morning
        assertEquals(at(2026, 9, 16, 14, 10), race.opensAt)
        assertEquals(at(2026, 9, 16, 19, 10), race.classStart)
    }

    @Test
    fun `the alarm fires two minutes early to unlock and reach the card`() {
        val race = Schedule.nextRace(bodyPower, at(2026, 9, 16, 9, 0))
        assertEquals(at(2026, 9, 16, 14, 8), race.alarmAt)
    }

    @Test
    fun `after the window it moves to next week`() {
        val race = Schedule.nextRace(bodyPower, at(2026, 9, 16, 14, 12))
        assertEquals(LocalDate.of(2026, 9, 23), race.date)
    }

    @Test
    fun `a window that opened seconds ago is still raced - a reboot at 14_10_20`() {
        val race = Schedule.nextRace(bodyPower, at(2026, 9, 16, 14, 10, 20))
        assertEquals(LocalDate.of(2026, 9, 16), race.date)
    }

    @Test
    fun `from another weekday it finds the coming wednesday`() {
        val race = Schedule.nextRace(bodyPower, at(2026, 9, 19, 12, 0)) // Saturday
        assertEquals(LocalDate.of(2026, 9, 23), race.date)
    }

    @Test
    fun `the day chip label matches the app's d dot M format`() {
        val race = Schedule.nextRace(bodyPower, at(2026, 11, 1, 9, 0))
        assertEquals("4.11", race.dayLabel)
    }

    @Test
    fun `an early class opens in the night - the case the user should double check`() {
        val feldenkrais = Watch("f", "פלדנקרייז", DayOfWeek.THURSDAY, LocalTime.of(7, 15))
        val race = Schedule.nextRace(feldenkrais, at(2026, 11, 18, 20, 0))
        assertEquals(at(2026, 11, 19, 2, 15), race.opensAt)
    }

    @Test
    fun `a different lead time is honoured`() {
        val race = Schedule.nextRace(bodyPower.copy(opensHoursBefore = 24), at(2026, 9, 14, 9, 0))
        assertEquals(at(2026, 9, 15, 19, 10), race.opensAt)
    }

    @Test
    fun `times are Israel time even when the phone is abroad`() {
        val inLondon = at(2026, 9, 16, 9, 0).withZoneSameInstant(java.time.ZoneId.of("Europe/London"))
        assertEquals(at(2026, 9, 16, 14, 10), Schedule.nextRace(bodyPower, inLondon).opensAt)
    }
}

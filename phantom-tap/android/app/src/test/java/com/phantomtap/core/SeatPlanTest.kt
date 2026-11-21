package com.phantomtap.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class SeatPlanTest {
    @Test
    fun `nearest orders by distance to the closest preference`() {
        assertEquals(listOf(4, 2, 30), SeatPlan.fallbackOrder(listOf(12, 13), setOf(2, 4, 30), SeatFallback.NEAREST))
    }

    @Test
    fun `ties break towards the lower number`() {
        assertEquals(listOf(11, 13), SeatPlan.fallbackOrder(listOf(12), setOf(13, 11), SeatFallback.NEAREST))
    }

    @Test
    fun `preferred seats are never repeated in the fallback`() {
        assertEquals(listOf(5), SeatPlan.fallbackOrder(listOf(12), setOf(12, 5), SeatFallback.NEAREST))
    }

    @Test
    fun `any takes free seats in list order`() {
        assertEquals(listOf(2, 4, 30), SeatPlan.fallbackOrder(listOf(12), setOf(30, 4, 2), SeatFallback.ANY))
    }

    @Test
    fun `none means preferences or nothing`() {
        assertEquals(emptyList<Int>(), SeatPlan.fallbackOrder(listOf(12), setOf(2, 4), SeatFallback.NONE))
    }

    @Test
    fun `seat preferences reject duplicate or impossible seats`() {
        assertThrows(IllegalArgumentException::class.java) { SeatPrefs(listOf(12, 12)) }
        assertThrows(IllegalArgumentException::class.java) { SeatPrefs(listOf(0)) }
    }
}

package com.phantomtap.core

import org.junit.Assert.assertEquals
import org.junit.Test

class SntpTest {
    @Test
    fun `offset and delay follow the NTP formulas`() {
        // Local clock 250ms slow, 40ms each way, server takes 2ms.
        val t1 = 1_000_000L
        val t2 = t1 + 250 + 40
        val t3 = t2 + 2
        val t4 = t1 + 40 + 2 + 40
        assertEquals(250L, Sntp.offset(t1, t2, t3, t4))
        assertEquals(80L, Sntp.delay(t1, t2, t3, t4))
    }

    @Test
    fun `median ignores one wild server`() {
        assertEquals(125L, Sntp.median(listOf(120L, 125L, 4_000L)))
        assertEquals(122L, Sntp.median(listOf(120L, 125L)))
    }

    @Test
    fun `reads an NTP timestamp as unix milliseconds`() {
        // 2026-01-01T00:00:00.500Z = unix 1767225600.5 s = NTP 3976214400.5 s
        val seconds = 3_976_214_400L
        val packet = ByteArray(48)
        fun put(at: Int, v: Long) { for (i in 0..3) packet[at + i] = (v shr (24 - 8 * i)).toByte() }
        put(40, seconds)
        put(44, 0x8000_0000L) // half a second
        assertEquals(1_767_225_600_500L, Sntp.readTimestamp(packet, 40))
    }
}

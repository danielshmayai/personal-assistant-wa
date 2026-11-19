package com.phantomtap.core

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/** Wall time and waiting, injectable so the driver can be tested in virtual time. */
interface TimeSource {
    fun now(): Long
    fun sleep(ms: Long)
}

/**
 * Phone time corrected against NTP.
 *
 * A phone's clock is usually network-synced and close, but "usually close" can
 * be a second out after a long flight mode or a carrier with bad NITZ. The
 * driver uses true time for one thing that matters: refreshing the timetable at
 * the exact minute in case the card does not flip by itself.
 */
class TrueClock(@Volatile var offsetMs: Long = 0L) : TimeSource {
    override fun now(): Long = System.currentTimeMillis() + offsetMs
    override fun sleep(ms: Long) {
        if (ms > 0) Thread.sleep(ms)
    }

    /**
     * Measure the offset against several servers and keep the median, so one
     * congested path cannot drag the answer. Returns false if none answered, in
     * which case the phone's own clock stays in use.
     */
    fun sync(servers: List<String> = Sntp.DEFAULT_SERVERS): Boolean {
        val offsets = servers.mapNotNull { runCatching { Sntp.query(it).offsetMs }.getOrNull() }
        if (offsets.isEmpty()) return false
        offsetMs = Sntp.median(offsets)
        return true
    }
}

/** SNTP, implemented directly: one UDP round trip and four timestamps. */
object Sntp {
    val DEFAULT_SERVERS = listOf("time.google.com", "pool.ntp.org", "time.cloudflare.com")

    private const val PORT = 123
    // NTP counts from 1900, unix time from 1970.
    private const val NTP_EPOCH_OFFSET_MS = 2_208_988_800_000L

    data class Sample(val offsetMs: Long, val delayMs: Long)

    /** Standard NTP: offset = ((t2 - t1) + (t3 - t4)) / 2. */
    fun offset(t1: Long, t2: Long, t3: Long, t4: Long): Long = ((t2 - t1) + (t3 - t4)) / 2

    /** Round trip minus server processing: (t4 - t1) - (t3 - t2). */
    fun delay(t1: Long, t2: Long, t3: Long, t4: Long): Long = (t4 - t1) - (t3 - t2)

    fun median(values: List<Long>): Long {
        require(values.isNotEmpty())
        val sorted = values.sorted()
        val mid = sorted.size / 2
        return if (sorted.size % 2 == 1) sorted[mid] else (sorted[mid - 1] + sorted[mid]) / 2
    }

    /** Read a 64-bit NTP timestamp at [at] as unix milliseconds. */
    fun readTimestamp(packet: ByteArray, at: Int): Long {
        val seconds = readUInt32(packet, at)
        val fraction = readUInt32(packet, at + 4)
        return seconds * 1000L + fraction * 1000L / 0x1_0000_0000L - NTP_EPOCH_OFFSET_MS
    }

    private fun readUInt32(b: ByteArray, at: Int): Long =
        ((b[at].toLong() and 0xff) shl 24) or
            ((b[at + 1].toLong() and 0xff) shl 16) or
            ((b[at + 2].toLong() and 0xff) shl 8) or
            (b[at + 3].toLong() and 0xff)

    fun query(server: String, timeoutMs: Int = 2000): Sample {
        val request = ByteArray(48).also { it[0] = 0x1B } // leap 0, version 3, mode 3 (client)
        DatagramSocket().use { socket ->
            socket.soTimeout = timeoutMs
            val t1 = System.currentTimeMillis()
            socket.send(DatagramPacket(request, request.size, InetAddress.getByName(server), PORT))
            val reply = ByteArray(48)
            socket.receive(DatagramPacket(reply, reply.size))
            val t4 = System.currentTimeMillis()
            val t2 = readTimestamp(reply, 32)
            val t3 = readTimestamp(reply, 40)
            check(t3 > 0) { "$server sent a zero transmit timestamp" }
            return Sample(offset(t1, t2, t3, t4), delay(t1, t2, t3, t4))
        }
    }
}

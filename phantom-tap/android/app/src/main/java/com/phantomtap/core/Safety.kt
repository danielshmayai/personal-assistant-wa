package com.phantomtap.core

/** Thrown inside a session the moment it is told to stop. */
class Stopped(reason: String) : RuntimeException(reason)

/**
 * Time that refuses to pass once a stop is requested.
 *
 * Every loop in the driver - waiting for the app, for the button, for a seat -
 * sleeps through its [TimeSource]. Checking the stop flag there means one check
 * point covers all of them, including loops not written yet: a stop takes effect
 * within one poll (80ms), whatever the driver was doing.
 */
class StoppableTime(private val base: TimeSource, private val stopReason: () -> String?) : TimeSource {
    override fun now(): Long = base.now()

    override fun sleep(ms: Long) {
        check()
        base.sleep(ms)
        check()
    }

    fun check() {
        stopReason()?.let { throw Stopped(it) }
    }
}

/**
 * The automatic brake for a runaway.
 *
 * A healthy week is a handful of sessions: one per favourite class, plus syncs.
 * Many sessions in one hour means something is re-triggering itself - a bug, a
 * loop of alarms, a sync that keeps re-adding - and the safe response is to stop
 * everything and tell the user, rather than to keep driving their phone.
 */
object RunawayGuard {
    /** Sessions allowed in any rolling hour. Two classes opening together plus syncs fit easily. */
    const val MAX_SESSIONS_PER_HOUR = 6

    /** Longest any session may run: 2 min early + 90 s wait + seats + a sync, with margin. */
    const val MAX_SESSION_MS = 6 * 60 * 1000L

    private const val HOUR_MS = 60 * 60 * 1000L

    data class Decision(val allowed: Boolean, val recentStarts: List<Long>, val reason: String? = null)

    /**
     * May a new session start now? [starts] are earlier session start times; the
     * returned list is what to remember, including this start if it is allowed.
     */
    fun admit(starts: List<Long>, now: Long): Decision {
        val recent = starts.filter { now - it in 0 until HOUR_MS }.sorted()
        if (recent.size >= MAX_SESSIONS_PER_HOUR) {
            return Decision(
                allowed = false,
                recentStarts = recent,
                reason = "${recent.size + 1} הפעלות בשעה האחרונה - יותר מדי, כנראה באג",
            )
        }
        return Decision(allowed = true, recentStarts = recent + now)
    }
}

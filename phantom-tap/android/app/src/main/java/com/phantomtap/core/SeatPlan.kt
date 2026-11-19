package com.phantomtap.core

import kotlin.math.abs

/**
 * Which seat to try next, once the preferred ones are gone.
 *
 * In the seat dialog free seats are red and taken ones gray, and the list only
 * shows a handful of rows at a time. So the preferred seats are tried one by one
 * first; only if all of them are taken does the driver read every free seat off
 * the list and hand them here to be ordered.
 */
object SeatPlan {

    /**
     * Free seats other than the preferred ones, best first.
     *
     * NEAREST orders by numeric distance to the closest preferred seat. On this
     * studio's floor map the numbers run along rows, so numeric distance tracks
     * physical distance well within a row and only roughly across rows - better
     * than an arbitrary pick, and not claimed to be more than that.
     */
    fun fallbackOrder(preferred: List<Int>, free: Collection<Int>, fallback: SeatFallback): List<Int> {
        if (fallback == SeatFallback.NONE) return emptyList()
        val rest = free.toSortedSet().filterNot { it in preferred }
        if (fallback == SeatFallback.ANY || preferred.isEmpty()) return rest
        return rest.sortedWith(compareBy({ seat -> preferred.minOf { abs(it - seat) } }, { it }))
    }
}

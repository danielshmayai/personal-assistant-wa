package com.phantomtap.core

import java.time.ZoneId

/** Timing constants shared by the driver, the scheduler and the reconciler. */
object Schedule {
    val ISRAEL: ZoneId = ZoneId.of("Asia/Jerusalem")

    /**
     * Two minutes is the budget for: the user unlocking the phone when prompted,
     * launching the app, opening the favourites and finding the card. All of that
     * happens before the window opens, so none of it costs time when it matters.
     */
    const val ARRIVE_EARLY_SECONDS = 120L

    /** If the button has not flipped 90s after the opening minute, it is not going to. */
    const val GIVE_UP_AFTER_SECONDS = 90L

    /**
     * Only used when a card does not show "פתיחת הרשמה": every class observed opened
     * registration exactly five hours before it started.
     */
    const val DEFAULT_LEAD_HOURS = 5L
}

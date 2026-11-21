package com.phantomtap.core

/**
 * The Holmes Place app's own on-screen words, read off the user's screenshots.
 *
 * The driver finds everything by these labels rather than by internal view ids,
 * which the app does not publish and can rename in any release. The visible
 * Hebrew is what a member sees, so it is also what changes least.
 */
object Labels {
    const val PACKAGE = "com.holmesplace"

    /** Bottom navigation tab with the class timetable. */
    const val TAB_STUDIO = "שיעורי סטודיו"

    /** The full timetable sub-tab. Not used for booking - favourites only. */
    const val SUBTAB_SCHEDULE = "לו\"ז שיעורים"

    /** The favourites sub-tab: the only source of classes to book. */
    const val SUBTAB_FAVORITES = "מועדפים"

    /** The sub-tab listing classes already booked. */
    const val SUBTAB_BOOKED = "מוזמנים"

    /** Card action once registration is open. */
    const val REGISTER = "הרשמה"

    /** Card action before registration opens. At the published minute it turns into REGISTER. */
    const val REMINDER = "תזכורת"

    /** Card action when the class is full. */
    const val WAITLIST = "הרשמה להמתנה"

    /** Title of the seat dialog that follows REGISTER on seated classes. */
    const val SEAT_DIALOG = "בחירת מקום"

    /** Confirms the seat. Disabled until a free seat is selected. */
    const val CONTINUE = "המשך"
}

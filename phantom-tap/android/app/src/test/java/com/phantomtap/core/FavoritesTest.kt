package com.phantomtap.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZonedDateTime

class FavoritesTest {
    private val now = ZonedDateTime.of(2026, 11, 19, 1, 0, 0, 0, Schedule.ISRAEL)

    private fun card(
        name: String = "BODY POWER",
        date: LocalDate = LocalDate.of(2026, 11, 25),
        start: LocalTime = LocalTime.of(19, 10),
        opensAt: ZonedDateTime? = ZonedDateTime.of(date, LocalTime.of(14, 10), Schedule.ISRAEL),
        action: String? = Labels.REMINDER,
        actionText: String? = action,
    ) = FavoriteCard(name, date, start, opensAt, action, actionText)

    private fun reconcile(current: List<Booking>, seen: List<FavoriteCard>, complete: Boolean = true) =
        Reconciler.reconcile(current, seen, complete, now)

    // ------------------------------------------------------------- reading --

    @Test
    fun `dates without a year land on the nearest one`() {
        val dec28 = LocalDate.of(2026, 12, 28)
        assertEquals(LocalDate.of(2027, 1, 3), FavoritesParser.inferYear(3, 1, dec28))
        assertEquals(LocalDate.of(2025, 12, 20), FavoritesParser.inferYear(20, 12, LocalDate.of(2026, 1, 3)))
        assertEquals(LocalDate.of(2026, 11, 25), FavoritesParser.inferYear(25, 11, LocalDate.of(2026, 11, 19)))
    }

    @Test
    fun `a card split into separate labels, range first, still reads its date and name`() {
        val card = Node(
            null,
            children = listOf(
                Node("19:10 - 20:00"),
                Node("|"),
                Node("25/11"),
                Node("|"),
                Node("רביעי"),
                Node("BODY POWER"),
                Node("סיון אריאלי / סטודיו 1"),
                Node("פתיחת הרשמה:"),
                Node("25/11 14:10"),
                Node(Labels.REMINDER, clickable = true),
            ),
        )
        val parsed = FavoritesParser.parse(card, LocalDate.of(2026, 11, 19))!!
        assertEquals("BODY POWER", parsed.className)
        assertEquals(LocalDate.of(2026, 11, 25), parsed.date)
        assertEquals(LocalTime.of(19, 10), parsed.start)
        assertEquals(ZonedDateTime.of(2026, 11, 25, 14, 10, 0, 0, Schedule.ISRAEL), parsed.opensAt)
    }

    @Test
    fun `the favourites tab is recognised only when the app marks it selected`() {
        val shown = Node(null, children = listOf(Node(Labels.SUBTAB_FAVORITES, selected = true)))
        val notShown = Node(null, children = listOf(Node(Labels.SUBTAB_FAVORITES, selected = false)))
        assertTrue(FavoritesParser.favoritesShowing(shown))
        assertFalse(FavoritesParser.favoritesShowing(notShown))
    }

    // ----------------------------------------------------------- reconcile --

    @Test
    fun `a new favourite is added with the opening time from its card`() {
        val result = reconcile(emptyList(), listOf(card()))

        val booking = result.added.single()
        assertEquals(BookingStatus.PENDING, booking.status)
        assertEquals(ZonedDateTime.of(2026, 11, 25, 14, 10, 0, 0, Schedule.ISRAEL), booking.opensAt)
        assertTrue(booking.opensAtFromCard)
    }

    @Test
    fun `a favourite already known is not added twice`() {
        val first = reconcile(emptyList(), listOf(card())).bookings
        val second = reconcile(first, listOf(card()))
        assertTrue(second.added.isEmpty())
        assertEquals(1, second.bookings.size)
    }

    @Test
    fun `a card without an opening line falls back to five hours before`() {
        val booking = reconcile(emptyList(), listOf(card(opensAt = null))).added.single()
        assertEquals(ZonedDateTime.of(2026, 11, 25, 14, 10, 0, 0, Schedule.ISRAEL), booking.opensAt)
        assertFalse(booking.opensAtFromCard)
    }

    @Test
    fun `a favourite already open is due now`() {
        val booking = reconcile(emptyList(), listOf(card(opensAt = null, action = Labels.REGISTER))).added.single()
        assertEquals(now, booking.opensAt)
    }

    @Test
    fun `an already registered favourite is skipped, not booked`() {
        val booking = reconcile(emptyList(), listOf(card(action = null, actionText = "ביטול הרשמה"))).bookings.single()
        assertEquals(BookingStatus.SKIPPED, booking.status)
        assertTrue(booking.detail.contains("ביטול הרשמה"))
    }

    @Test
    fun `a class that already started is ignored`() {
        val started = card(date = LocalDate.of(2026, 11, 18), start = LocalTime.of(20, 0))
        assertTrue(reconcile(emptyList(), listOf(started)).bookings.isEmpty())
    }

    @Test
    fun `a moved opening time is followed`() {
        val known = reconcile(emptyList(), listOf(card())).bookings
        val moved = ZonedDateTime.of(2026, 11, 25, 13, 10, 0, 0, Schedule.ISRAEL)
        val result = reconcile(known, listOf(card(opensAt = moved)))
        assertEquals(moved, result.updated.single().opensAt)
    }

    @Test
    fun `an un-hearted class is removed after a complete read`() {
        val known = reconcile(emptyList(), listOf(card(), card(name = "HIIT"))).bookings
        val result = reconcile(known, listOf(card()), complete = true)
        assertEquals(listOf("HIIT"), result.removed.map { it.className })
    }

    @Test
    fun `a partial read never removes anything - what is off screen is unknown, not gone`() {
        val known = reconcile(emptyList(), listOf(card(), card(name = "HIIT"))).bookings
        val result = reconcile(known, listOf(card()), complete = false)
        assertTrue(result.removed.isEmpty())
        assertEquals(2, result.bookings.size)
    }

    @Test
    fun `a finished booking is kept even if its favourite is gone`() {
        val booked = reconcile(emptyList(), listOf(card())).bookings.map { it.copy(status = BookingStatus.BOOKED) }
        val result = reconcile(booked, emptyList(), complete = true)
        assertTrue(result.removed.isEmpty())
        assertEquals(BookingStatus.BOOKED, result.bookings.single().status)
    }

    @Test
    fun `bookings for classes over a day ago are forgotten`() {
        val old = Booking(
            "HIIT", LocalDate.of(2026, 11, 10), LocalTime.of(18, 30),
            ZonedDateTime.of(2026, 11, 10, 13, 30, 0, 0, Schedule.ISRAEL), true, BookingStatus.BOOKED,
        )
        assertTrue(reconcile(listOf(old), emptyList(), complete = false).bookings.isEmpty())
    }
}

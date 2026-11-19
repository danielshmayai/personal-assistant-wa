package com.phantomtap.core

/** A rectangle in screen pixels. */
data class Bounds(val left: Int, val top: Int, val right: Int, val bottom: Int) {
    val centerX: Int get() = (left + right) / 2
    val centerY: Int get() = (top + bottom) / 2
    val height: Int get() = bottom - top
}

/**
 * One element on screen, as a plain value.
 *
 * The driver reasons only about these, never about Android's
 * AccessibilityNodeInfo directly. That keeps every decision it makes - which
 * card, which button, which seat - testable without a phone. [ref] carries the
 * platform object back to the [Screen] that produced it, for acting on.
 */
class Node(
    val text: String?,
    val enabled: Boolean = true,
    val clickable: Boolean = false,
    val scrollable: Boolean = false,
    val bounds: Bounds = Bounds(0, 0, 0, 0),
    val children: List<Node> = emptyList(),
    val ref: Any? = null,
) {
    var parent: Node? = null
        private set

    init {
        children.forEach { it.parent = this }
    }

    /** The visible text, trimmed; null when there is none. */
    val label: String? get() = text?.trim()?.takeIf { it.isNotEmpty() }

    fun descendants(): Sequence<Node> = sequence {
        yield(this@Node)
        children.forEach { yieldAll(it.descendants()) }
    }

    fun ancestors(): Sequence<Node> = generateSequence(parent) { it.parent }

    /** All text in this subtree, joined - what a sighted user reads in this region. */
    fun subtreeText(): String = descendants().mapNotNull { it.label }.joinToString(" ")

    fun find(predicate: (Node) -> Boolean): Node? = descendants().firstOrNull(predicate)

    fun hasExact(label: String): Boolean = find { it.label == label } != null

    override fun toString(): String = "Node(${label ?: "-"}${if (!enabled) ", disabled" else ""})"
}

/**
 * The phone's screen, as far as the driver is concerned.
 *
 * On a phone this is backed by an AccessibilityService; in the tests by a fake
 * that behaves like the Holmes Place screens in the user's screenshots.
 */
interface Screen {
    /** Package of the app in the foreground, or null when it cannot be read. */
    fun foregroundPackage(): String?

    /** The current foreground window, or null when it cannot be read right now. */
    fun snapshot(): Node?

    /** Tap [node] (or its nearest clickable ancestor). */
    fun click(node: Node): Boolean

    /** Scroll a scrollable [node] one page. Returns false at the end of its content. */
    fun scroll(node: Node, forward: Boolean): Boolean

    /** Drag down inside [area] - the app's pull-to-refresh. */
    fun pullToRefresh(area: Bounds): Boolean

    /** Bring the Holmes Place app to the front. */
    fun launchApp(): Boolean
}

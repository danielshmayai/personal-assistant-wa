package com.phantomtap.app

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.Rect
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.phantomtap.core.Bounds
import com.phantomtap.core.FavoritesParser
import com.phantomtap.core.Labels
import com.phantomtap.core.Node
import com.phantomtap.core.Schedule
import com.phantomtap.core.Screen
import java.time.LocalDate
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * The part that sees the Holmes Place screen and taps it.
 *
 * Scoped in accessibility_service_config.xml to com.holmesplace only. It taps
 * only when a booking session drives it through [A11yScreen]. On its own it does
 * one thing: when the user is looking at the "מועדפים" tab, it reads the cards on
 * screen and adds any new favourite - reading only, never tapping.
 */
class HolmesA11yService : AccessibilityService() {
    private val main = Handler(Looper.getMainLooper())
    private var keepAwakeView: View? = null
    private lateinit var reader: Handler
    private val passiveRead = Runnable { readFavouritesOnScreen() }

    override fun onServiceConnected() {
        instance = this
        reader = Handler(HandlerThread("phantom-tap-reader").apply { start() }.looper)
    }

    /**
     * Debounced: read once the screen has been still for a moment, not on every
     * scroll event - converting the whole window on each one would slow the
     * Holmes Place app down while the user is using it.
     */
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.packageName?.toString() != Labels.PACKAGE || BookingService.busy || !::reader.isInitialized) return
        reader.removeCallbacks(passiveRead)
        reader.postDelayed(passiveRead, PASSIVE_DELAY_MS)
    }

    /**
     * Add and update only. What is off screen is unknown, not gone, so a passive
     * read never removes a booking - only a full sync does.
     */
    private fun readFavouritesOnScreen() {
        if (BookingService.busy || !Automation.isEnabled(this)) return
        runCatching {
            val root = A11yScreen(this).snapshot() ?: return
            if (!FavoritesParser.favoritesShowing(root)) return
            val cards = FavoritesParser.cards(root, LocalDate.now(Schedule.ISRAEL)).map { it.first }
            if (cards.isNotEmpty()) FavoritesSync.apply(this, cards, complete = false)
        }
    }

    override fun onInterrupt() = Unit

    override fun onUnbind(intent: Intent?): Boolean {
        instance = null
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        keepScreenOn(false)
        if (::reader.isInitialized) reader.looper.quitSafely()
        instance = null
        super.onDestroy()
    }

    /** Tap at a point. Blocks until done; never call on the main thread. */
    fun tap(x: Float, y: Float): Boolean = gesture(Path().apply { moveTo(x, y) }, 60)

    /** Drag from one point to another. Blocks until done; never call on the main thread. */
    fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long): Boolean =
        gesture(Path().apply { moveTo(x1, y1); lineTo(x2, y2) }, durationMs)

    private fun gesture(path: Path, durationMs: Long): Boolean {
        check(Looper.myLooper() != Looper.getMainLooper()) { "gestures block; run them off the main thread" }
        val finished = CountDownLatch(1)
        var completed = false
        val stroke = GestureDescription.StrokeDescription(path, 0, durationMs)
        val description = GestureDescription.Builder().addStroke(stroke).build()
        main.post {
            val accepted = dispatchGesture(description, object : GestureResultCallback() {
                override fun onCompleted(gestureDescription: GestureDescription?) {
                    completed = true
                    finished.countDown()
                }

                override fun onCancelled(gestureDescription: GestureDescription?) {
                    finished.countDown()
                }
            }, main)
            if (!accepted) finished.countDown()
        }
        finished.await(durationMs + 2_000, TimeUnit.MILLISECONDS)
        return completed
    }

    /**
     * Keep the screen on while a booking runs.
     *
     * The run starts two minutes before the window and the screen would time out
     * - and the phone lock - in that time. A 1-pixel, untouchable accessibility
     * overlay with FLAG_KEEP_SCREEN_ON holds it on without any extra permission,
     * and is removed the moment the run ends.
     */
    fun keepScreenOn(on: Boolean) {
        main.post {
            val wm = getSystemService(WindowManager::class.java)
            if (on && keepAwakeView == null) {
                val view = View(this)
                val params = WindowManager.LayoutParams(
                    1,
                    1,
                    WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                        WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                        WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
                    PixelFormat.TRANSLUCENT,
                ).apply { gravity = Gravity.TOP or Gravity.START }
                wm.addView(view, params)
                keepAwakeView = view
            } else if (!on) {
                keepAwakeView?.let { runCatching { wm.removeView(it) } }
                keepAwakeView = null
            }
        }
    }

    companion object {
        private const val PASSIVE_DELAY_MS = 800L

        @Volatile
        var instance: HolmesA11yService? = null
            private set

        /** Whether the user has switched the service on in Settings > Accessibility. */
        fun isEnabled(context: Context): Boolean {
            val enabled = Settings.Secure.getString(
                context.contentResolver,
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
            ) ?: return false
            val me = ComponentName(context, HolmesA11yService::class.java)
            return enabled.split(':').any { ComponentName.unflattenFromString(it) == me }
        }
    }
}

/** The driver's [Screen], backed by the accessibility service. */
class A11yScreen(private val service: HolmesA11yService) : Screen {

    override fun foregroundPackage(): String? = service.rootInActiveWindow?.packageName?.toString()

    override fun snapshot(): Node? = service.rootInActiveWindow?.let { convert(it, 0) }

    private fun convert(info: AccessibilityNodeInfo, depth: Int): Node {
        val children = if (depth >= MAX_DEPTH) {
            emptyList()
        } else {
            (0 until info.childCount).mapNotNull { i -> info.getChild(i)?.let { convert(it, depth + 1) } }
        }
        val rect = Rect().also(info::getBoundsInScreen)
        return Node(
            // Some apps put their visible label in contentDescription instead of text.
            text = (info.text ?: info.contentDescription)?.toString(),
            enabled = info.isEnabled,
            clickable = info.isClickable,
            scrollable = info.isScrollable,
            selected = info.isSelected,
            bounds = Bounds(rect.left, rect.top, rect.right, rect.bottom),
            children = children,
            ref = info,
        )
    }

    override fun click(node: Node): Boolean {
        val info = node.ref as? AccessibilityNodeInfo ?: return false
        // The label is often a TextView inside the clickable button or row.
        var target: AccessibilityNodeInfo? = info
        while (target != null && !target.isClickable) target = target.parent
        if (target?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true) return true
        // Nothing clickable in the semantics tree: tap where the label is drawn.
        return service.tap(node.bounds.centerX.toFloat(), node.bounds.centerY.toFloat())
    }

    override fun scroll(node: Node, forward: Boolean): Boolean {
        val info = node.ref as? AccessibilityNodeInfo ?: return false
        val action = if (forward) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
        return info.performAction(action)
    }

    override fun pullToRefresh(area: Bounds): Boolean {
        val x = area.centerX.toFloat()
        val from = area.top + area.height * 0.15f
        val to = area.top + area.height * 0.70f
        return service.swipe(x, from, x, to, 350)
    }

    override fun launchApp(): Boolean {
        val intent = service.packageManager.getLaunchIntentForPackage(Labels.PACKAGE) ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
        service.startActivity(intent)
        return true
    }

    private companion object {
        const val MAX_DEPTH = 40
    }
}

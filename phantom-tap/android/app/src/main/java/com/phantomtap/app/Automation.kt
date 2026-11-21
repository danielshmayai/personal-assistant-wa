package com.phantomtap.app

import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import com.phantomtap.R
import com.phantomtap.core.RunawayGuard
import org.json.JSONArray

/**
 * The one switch that stops everything.
 *
 * Off means: the running session is stopped within a fraction of a second, every
 * alarm is cancelled, no new session starts, and nothing is read in the
 * background - until the user switches it back on. It is flipped from the app,
 * from the "עצור הכל" button on every phantom-tap notification, from the Quick
 * Settings tile, or by the runaway guard itself.
 */
object Automation {
    private const val PREFS = "phantom_tap_safety"
    private val LOCK = Any()

    private fun prefs(context: Context) = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun isEnabled(context: Context): Boolean = prefs(context).getBoolean("enabled", true)

    /** Why it was switched off, for the app to show; null while on. */
    fun disabledReason(context: Context): String? = prefs(context).getString("reason", null)

    fun disable(context: Context, reason: String) {
        synchronized(LOCK) {
            prefs(context).edit().putBoolean("enabled", false).putString("reason", reason).commit()
        }
        BookingService.stopAll(reason)
        BookingScheduler.cancelAll(context)
        Notifier.disabled(context, reason)
        KillSwitchTile.refresh(context)
    }

    fun enable(context: Context) {
        synchronized(LOCK) {
            // A fresh start: the burst that tripped the guard must not trip it again.
            prefs(context).edit().putBoolean("enabled", true).remove("reason").putString("starts", "[]").commit()
        }
        BookingScheduler.rescheduleAll(context)
        Notifier.cancelDisabled(context)
        KillSwitchTile.refresh(context)
    }

    /**
     * May a session start? Every session asks first. False when switched off, or
     * when this start would be one too many for the hour - in which case the guard
     * switches everything off on the spot.
     */
    fun admitSession(context: Context): Boolean {
        val tripped: String? = synchronized(LOCK) {
            if (!isEnabled(context)) return false
            val p = prefs(context)
            val starts = runCatching {
                val a = JSONArray(p.getString("starts", "[]"))
                (0 until a.length()).map { a.getLong(it) }
            }.getOrDefault(emptyList())
            val decision = RunawayGuard.admit(starts, System.currentTimeMillis())
            p.edit().putString("starts", JSONArray(decision.recentStarts).toString()).commit()
            if (decision.allowed) null else decision.reason
        }
        if (tripped != null) {
            disable(context, "הושבת אוטומטית: $tripped")
            return false
        }
        return true
    }
}

/** The "עצור הכל" button on phantom-tap's notifications. */
class StopReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Automation.disable(context, "עצרת ידנית")
    }
}

/**
 * A Quick Settings tile: on/off from the notification shade.
 *
 * The one control that stays in reach while phantom-tap is driving the screen -
 * the shade can always be pulled down, whatever app is in front.
 */
class KillSwitchTile : TileService() {
    override fun onStartListening() = render()

    override fun onClick() {
        if (Automation.isEnabled(this)) Automation.disable(this, "עצרת מההגדרות המהירות") else Automation.enable(this)
        render()
    }

    private fun render() {
        val tile = qsTile ?: return
        val on = Automation.isEnabled(this)
        tile.state = if (on) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        tile.label = "phantom-tap"
        tile.icon = Icon.createWithResource(this, R.drawable.ic_stat)
        if (Build.VERSION.SDK_INT >= 29) tile.subtitle = if (on) "פעיל" else "מושבת"
        tile.updateTile()
    }

    companion object {
        fun refresh(context: Context) {
            runCatching { requestListeningState(context, ComponentName(context, KillSwitchTile::class.java)) }
        }
    }
}

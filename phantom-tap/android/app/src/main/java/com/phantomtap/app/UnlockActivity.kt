package com.phantomtap.app

import android.app.Activity
import android.app.KeyguardManager
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.WindowManager
import android.widget.TextView

/**
 * Shown over the lock screen two minutes before a window, to ask for an unlock.
 *
 * It cannot unlock the phone itself - nothing can, and nothing should. It wakes
 * the screen and brings up the system's own unlock prompt (fingerprint, face or
 * PIN). Once the phone is unlocked it gets out of the way.
 */
class UnlockActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        setContentView(
            TextView(this).apply {
                text = "phantom-tap\n\nפתח את הנעילה.\nההרשמה לשיעור נפתחת בעוד רגע, והאפליקציה תירשם בשבילך."
                textSize = 22f
                gravity = Gravity.CENTER
                setPadding(48, 48, 48, 48)
            },
        )

        getSystemService(KeyguardManager::class.java).requestDismissKeyguard(
            this,
            object : KeyguardManager.KeyguardDismissCallback() {
                override fun onDismissSucceeded() = finish()
            },
        )
    }
}

package com.spirolife.savvy.notifications

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.withStateAtLeast
import com.spirolife.savvy.Graph
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * Android 13+ asks before an app may post notifications. Savvy asks once, the
 * first time the phone is paired (that's when there is something to show);
 * after that the user changes it in system settings.
 * Call [requestOnceAfterPairing] from the activity's onCreate.
 */
object NotificationPermission {
    private const val KEY_ASKED = "asked_post_notifications"

    fun requestOnceAfterPairing(activity: ComponentActivity) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val flags = activity.getSharedPreferences("savvy_notifications", Context.MODE_PRIVATE)
        if (flags.getBoolean(KEY_ASKED, false) || granted(activity)) return
        val launcher = activity.registerForActivityResult(ActivityResultContracts.RequestPermission()) {}
        activity.lifecycleScope.launch {
            Graph.prefs.values.first { it.token != null }
            activity.lifecycle.withStateAtLeast(Lifecycle.State.RESUMED) {
                if (!granted(activity) && !flags.getBoolean(KEY_ASKED, false)) {
                    flags.edit().putBoolean(KEY_ASKED, true).apply()
                    launcher.launch(Manifest.permission.POST_NOTIFICATIONS)
                }
            }
        }
    }

    private fun granted(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
}

package com.spirolife.savvy

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.mutableStateOf
import com.spirolife.savvy.notifications.NotificationPermission
import com.spirolife.savvy.ui.nav.Routes
import com.spirolife.savvy.ui.nav.SavvyNavHost
import com.spirolife.savvy.ui.theme.SavvyTheme

class MainActivity : ComponentActivity() {
    /** A route a notification asked to open (extra [EXTRA_ROUTE]); consumed by the nav host. */
    private val pendingRoute = mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        NotificationPermission.requestOnceAfterPairing(this)   // Android 13+: asked once, after pairing
        pendingRoute.value = intent?.getStringExtra(EXTRA_ROUTE)
        setContent {
            SavvyTheme {
                SavvyNavHost(
                    startRoute = if (Graph.isPaired) Routes.TODAY else Routes.PAIR,
                    pendingRoute = pendingRoute.value,
                    onPendingRouteHandled = { pendingRoute.value = null },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.getStringExtra(EXTRA_ROUTE)?.let { pendingRoute.value = it }
    }

    companion object {
        /** Put a [Routes] value here on a PendingIntent to open that screen. */
        const val EXTRA_ROUTE = "com.spirolife.savvy.ROUTE"
    }
}

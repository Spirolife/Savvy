package com.spirolife.savvy.notifications

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.spirolife.savvy.Graph
import com.spirolife.savvy.data.api.AppNotification
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking

/**
 * Receives FCM data messages {"id", "kind", "title", "body"} (docs/app-api.md,
 * Notifications) and new tokens. Only runs when the app was built with
 * google-services.json.
 */
class SavvyMessagingService : FirebaseMessagingService() {
    override fun onMessageReceived(message: RemoteMessage) {
        val pushed = parse(message.data) ?: return
        if (!Graph.isPaired) return
        // onMessageReceived runs on a background thread with about 10 seconds to finish.
        runBlocking { Notifications.sync(applicationContext).onPush(pushed, timeoutMs = 6_000) }
    }

    override fun onNewToken(token: String) {
        Graph.appScope.launch { Notifications.registerToken(token) }
    }

    companion object {
        fun parse(data: Map<String, String>): AppNotification? {
            val id = data["id"]?.toLongOrNull() ?: return null
            return AppNotification(
                id = id, ts = "", kind = data["kind"] ?: "checkin",
                title = data["title"] ?: "Savvy", body = data["body"].orEmpty(),
            )
        }
    }
}

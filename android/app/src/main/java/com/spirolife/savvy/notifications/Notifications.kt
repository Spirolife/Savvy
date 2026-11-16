package com.spirolife.savvy.notifications

import android.app.Activity
import android.app.Application
import android.content.Context
import android.os.Bundle
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import com.spirolife.savvy.Graph
import com.spirolife.savvy.data.api.ApiException
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * How Savvy reaches the phone (docs/app-api.md, Notifications).
 *
 * - FCM push when the app was built with google-services.json: the token is
 *   sent to the PC with POST /v1/devices/fcm, here and in onNewToken.
 * - Polling GET /v1/notifications: every 15 minutes (WorkManager, network
 *   required) and whenever the app comes to the foreground. It runs even with
 *   FCM, because the PC may not have its Firebase key; [NotificationSync]
 *   makes sure nothing is shown twice.
 *
 * Both start when the phone is paired and stop when it is unpaired.
 */
object Notifications {
    private const val TAG = "SavvyNotifications"
    private const val POLL_WORK = "savvy-notification-poll"
    private const val STATE_PREFS = "savvy_notifications"
    private const val KEY_BASELINED_FOR = "baselined_for"

    @Volatile private var syncInstance: NotificationSync? = null

    fun init(context: Context) {
        val app = context.applicationContext
        NotificationPoster(app).createChannels()
        if (!prefsReady()) return
        Graph.appScope.launch {
            Graph.prefs.values
                .map { it.token != null && it.baseUrl != null }
                .distinctUntilChanged()
                .collect { paired -> if (paired) onPaired(app) else onUnpaired(app) }
        }
        (app as? Application)?.registerActivityLifecycleCallbacks(ForegroundPoller(app))
    }

    /** The one sync, shared by polling, pushes and foreground checks. */
    fun sync(context: Context): NotificationSync = syncInstance ?: synchronized(this) {
        syncInstance ?: NotificationSync(
            fetch = { after, limit -> Graph.api.notifications(after, limit) },
            state = PrefsSyncState(context.applicationContext),
            post = NotificationPoster(context)::post,
        ).also { syncInstance = it }
    }

    /** Polls now, quietly; for callers that don't care about failures. */
    suspend fun pollNow(context: Context) {
        if (!Graph.isPaired) return
        try {
            sync(context).sync()
        } catch (e: IOException) {
            Log.i(TAG, "Couldn't reach the PC for notifications: ${e.message}")
        } catch (e: ApiException) {
            Log.w(TAG, "Notifications feed: ${e.message}")
        }
    }

    fun firebaseAvailable(context: Context): Boolean =
        try { FirebaseApp.getApps(context).isNotEmpty() } catch (_: Throwable) { false }

    /** Sends an FCM token to the PC; a failure is retried at the next start or token change. */
    suspend fun registerToken(token: String) {
        if (!Graph.isPaired) return
        try {
            Graph.api.registerFcm(token)
        } catch (e: Exception) {
            if (e is kotlinx.coroutines.CancellationException) throw e
            Log.w(TAG, "Couldn't register for push: ${e.message}")
        }
    }

    private suspend fun onPaired(context: Context) {
        // Skip past what was on the PC before this pairing, then start listening.
        pollNow(context)
        schedulePolling(context)
        if (firebaseAvailable(context)) {
            try {
                registerToken(FirebaseMessaging.getInstance().token.await())
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                Log.w(TAG, "No FCM token: ${e.message}")
            }
        }
    }

    private fun onUnpaired(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(POLL_WORK)
    }

    private fun schedulePolling(context: Context) {
        val request = PeriodicWorkRequestBuilder<PollWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(context)
            .enqueueUniquePeriodicWork(POLL_WORK, ExistingPeriodicWorkPolicy.KEEP, request)
    }

    private fun prefsReady(): Boolean = try { Graph.prefs; true } catch (_: UninitializedPropertyAccessException) { false }

    /**
     * Progress lives in AppPrefs.lastNotificationId (cleared on unpair). The
     * "history skipped" flag is keyed to the device token, so pairing again
     * (a new token) skips the PC's backlog again.
     */
    private class PrefsSyncState(context: Context) : SyncState {
        private val flags = context.getSharedPreferences(STATE_PREFS, Context.MODE_PRIVATE)
        private fun tokenKey(): String = Graph.prefs.token?.hashCode()?.toString() ?: ""

        override fun lastId(): Long = Graph.prefs.lastNotificationId
        override suspend fun setLastId(id: Long) = Graph.prefs.setLastNotificationId(id)
        override fun baselined(): Boolean = tokenKey().isNotEmpty() && flags.getString(KEY_BASELINED_FOR, null) == tokenKey()
        override fun setBaselined() { flags.edit().putString(KEY_BASELINED_FOR, tokenKey()).apply() }
    }

    /** Polls when the first activity starts, i.e. the app comes to the foreground. */
    private class ForegroundPoller(private val app: Context) : Application.ActivityLifecycleCallbacks {
        private var started = 0
        override fun onActivityStarted(activity: Activity) {
            if (started++ == 0) Graph.appScope.launch { pollNow(app) }
        }
        override fun onActivityStopped(activity: Activity) { if (started > 0) started-- }
        override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) = Unit
        override fun onActivityResumed(activity: Activity) = Unit
        override fun onActivityPaused(activity: Activity) = Unit
        override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) = Unit
        override fun onActivityDestroyed(activity: Activity) = Unit
    }
}

/** The 15-minute poll. */
class PollWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        if (!Graph.isPaired) return Result.success()
        return try {
            Notifications.sync(applicationContext).sync()
            Result.success()
        } catch (_: IOException) {
            Result.success()     // PC asleep or off Tailscale: the next period tries again
        } catch (_: ApiException) {
            Result.success()
        }
    }
}

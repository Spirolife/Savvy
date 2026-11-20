package com.spirolife.savvy.notifications

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.RemoteInput
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.spirolife.savvy.Graph
import com.spirolife.savvy.data.api.AppNotification
import com.spirolife.savvy.data.api.ChatEvent
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.withTimeout

/** The "Reply" button on a notification: hands the text to [ReplyWorker], which may take a while. */
class ReplyReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val text = RemoteInput.getResultsFromIntent(intent)?.getCharSequence(KEY_TEXT)?.toString()?.trim()
        val original = fromExtras(intent) ?: return
        val poster = NotificationPoster(context)
        if (text.isNullOrEmpty()) {
            poster.post(original)           // clears the reply spinner
            return
        }
        poster.showReply(original, text, answer = null, error = null)
        val data = Data.Builder()
            .putLong(EXTRA_ID, original.id).putString(EXTRA_KIND, original.kind)
            .putString(EXTRA_TITLE, original.title).putString(EXTRA_BODY, original.body.take(2000))  // Data caps at 10 KB
            .putString(KEY_TEXT, text.take(4000))
            .build()
        WorkManager.getInstance(context).enqueue(
            OneTimeWorkRequestBuilder<ReplyWorker>()
                .setInputData(data)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
        )
    }

    companion object {
        const val KEY_TEXT = "savvy_reply_text"
        private const val ACTION = "com.spirolife.savvy.REPLY"
        private const val EXTRA_ID = "id"
        private const val EXTRA_KIND = "kind"
        private const val EXTRA_TITLE = "title"
        private const val EXTRA_BODY = "body"

        fun intent(context: Context, n: AppNotification): Intent =
            Intent(context, ReplyReceiver::class.java).setAction(ACTION)
                .putExtra(EXTRA_ID, n.id).putExtra(EXTRA_KIND, n.kind)
                .putExtra(EXTRA_TITLE, n.title).putExtra(EXTRA_BODY, n.body)

        private fun fromExtras(intent: Intent): AppNotification? {
            val id = intent.getLongExtra(EXTRA_ID, -1)
            if (id < 0) return null
            return AppNotification(id, "", intent.getStringExtra(EXTRA_KIND) ?: "checkin",
                intent.getStringExtra(EXTRA_TITLE).orEmpty(), intent.getStringExtra(EXTRA_BODY).orEmpty())
        }

        internal fun fromData(data: Data): AppNotification? {
            val id = data.getLong(EXTRA_ID, -1)
            if (id < 0) return null
            return AppNotification(id, "", data.getString(EXTRA_KIND) ?: "checkin",
                data.getString(EXTRA_TITLE).orEmpty(), data.getString(EXTRA_BODY).orEmpty())
        }
    }
}

/** Sends a notification reply as a chat turn and shows Savvy's answer in the same notification. */
class ReplyWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val original = ReplyReceiver.fromData(inputData) ?: return Result.failure()
        val text = inputData.getString(ReplyReceiver.KEY_TEXT) ?: return Result.failure()
        val poster = NotificationPoster(applicationContext)
        if (!Graph.isPaired) {
            poster.showReply(original, text, null, "Couldn't send: this phone isn't paired with Savvy.")
            return Result.failure()
        }
        val outcome = runCatching {
            withTimeout(5 * 60_000L) {
                collectReply(Graph.api.chat(text)) { id -> Graph.api.confirm(id, allow = false) }
            }
        }
        outcome.fold(
            onSuccess = { poster.showReply(original, text, it, null) },
            onFailure = { poster.showReply(original, text, null, "Couldn't reach Savvy: ${it.message ?: "unknown error"}. Open the app to try again.") },
        )
        return Result.success()
    }

    companion object {
        /**
         * Runs a chat turn to the end and returns Savvy's reply. A reply from the
         * notification shade has nobody to approve an action, so confirmations are
         * declined and the reply says so. Throws on an error event.
         */
        suspend fun collectReply(events: Flow<ChatEvent>, decline: suspend (String) -> Unit): String {
            val text = StringBuilder()
            var done: String? = null
            val declined = mutableListOf<String>()
            events.collect { e ->
                when (e) {
                    is ChatEvent.Text -> text.append(e.delta)
                    is ChatEvent.Confirm -> { declined += e.name; decline(e.id) }
                    is ChatEvent.Done -> done = e.reply
                    is ChatEvent.Error -> throw IllegalStateException(e.message)
                    is ChatEvent.Tool, is ChatEvent.Start -> Unit
                }
            }
            var reply = (done ?: text.toString()).trim().ifEmpty { "(no reply)" }
            if (declined.isNotEmpty()) {
                reply += "\n\n(Savvy wanted to ${declined.joinToString(", ")}; open the app to approve actions like that.)"
            }
            return reply
        }
    }
}

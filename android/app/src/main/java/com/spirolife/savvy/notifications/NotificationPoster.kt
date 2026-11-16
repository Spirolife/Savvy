package com.spirolife.savvy.notifications

import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.RemoteInput
import com.spirolife.savvy.MainActivity
import com.spirolife.savvy.R
import com.spirolife.savvy.data.api.AppNotification

/** Channels and the notifications themselves. */
class NotificationPoster(context: Context) {
    private val context = context.applicationContext
    private val manager = NotificationManagerCompat.from(this.context)

    fun createChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val system = context.getSystemService(NotificationManager::class.java) ?: return
        system.createNotificationChannels(listOf(
            NotificationChannel(NotificationKinds.CHANNEL_CHECKINS, "Check-ins and plans", NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = "Morning and evening prompts, check-ins, weekly review, meal plans" },
            NotificationChannel(NotificationKinds.CHANNEL_REMINDERS, "Reminders", NotificationManager.IMPORTANCE_HIGH)
                .apply { description = "Reminders you asked Savvy for, and rest-day warnings" },
            NotificationChannel(NotificationKinds.CHANNEL_ALERTS, "Alerts", NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = "Savvy's services starting or failing, Google accounts that need signing in" },
        ))
    }

    fun post(n: AppNotification) {
        val builder = base(n.kind, n.id)
            .setContentTitle(n.title)
            .setContentText(n.body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(n.body))
        if (NotificationKinds.allowsReply(n.kind)) builder.addAction(replyAction(n))
        show(n.id, builder)
    }

    /** After a direct reply: show the exchange so far (or why it failed), keep the reply button. */
    fun showReply(original: AppNotification, said: String, answer: String?, error: String?) {
        val text = buildString {
            append("You: ").append(said)
            append("\n\n")
            append(if (answer != null) "Savvy: $answer" else (error ?: "Sending…"))
        }
        val builder = base(original.kind, original.id)
            .setContentTitle(original.title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setOnlyAlertOnce(true)
        if (answer != null || error != null) builder.addAction(replyAction(original.copy(body = answer ?: original.body)))
        show(original.id, builder)
    }

    private fun base(kind: String, id: Long): NotificationCompat.Builder =
        NotificationCompat.Builder(context, NotificationKinds.channelFor(kind))
            .setSmallIcon(R.drawable.ic_notification)
            .setAutoCancel(true)
            .setPriority(if (NotificationKinds.channelFor(kind) == NotificationKinds.CHANNEL_REMINDERS)
                NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
            .setContentIntent(openIntent(kind, id))

    private fun openIntent(kind: String, id: Long): PendingIntent {
        val intent = Intent(context, MainActivity::class.java)
            .putExtra(MainActivity.EXTRA_ROUTE, NotificationKinds.routeFor(kind))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        return PendingIntent.getActivity(context, requestCode(id), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun replyAction(n: AppNotification): NotificationCompat.Action {
        val intent = ReplyReceiver.intent(context, n)
        // RemoteInput fills in the reply, so this one must be mutable.
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or
            (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0)
        val pending = PendingIntent.getBroadcast(context, requestCode(n.id), intent, flags)
        val input = RemoteInput.Builder(ReplyReceiver.KEY_TEXT).setLabel("Reply to Savvy").build()
        return NotificationCompat.Action.Builder(R.drawable.ic_notification, "Reply", pending)
            .addRemoteInput(input)
            .setAllowGeneratedReplies(false)
            .build()
    }

    @SuppressLint("MissingPermission")   // checked just above; denial just means nothing shows
    private fun show(id: Long, builder: NotificationCompat.Builder) {
        if (!manager.areNotificationsEnabled()) return
        try {
            manager.notify(TAG, requestCode(id), builder.build())
        } catch (_: SecurityException) {
        }
    }

    companion object {
        private const val TAG = "savvy"
        fun requestCode(id: Long): Int = (id % Int.MAX_VALUE).toInt()
    }
}

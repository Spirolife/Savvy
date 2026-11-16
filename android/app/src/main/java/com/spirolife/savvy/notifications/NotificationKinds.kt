package com.spirolife.savvy.notifications

import com.spirolife.savvy.ui.nav.Routes

/**
 * How each notification kind from the PC (docs/app-api.md, Notifications) is
 * shown: which channel it goes to, which screen a tap opens, and whether it
 * offers a direct reply. Pure, so it is unit tested.
 */
object NotificationKinds {
    const val CHANNEL_CHECKINS = "checkins"
    const val CHANNEL_REMINDERS = "reminders"
    const val CHANNEL_ALERTS = "alerts"

    fun channelFor(kind: String): String = when (kind) {
        "reminder", "rest" -> CHANNEL_REMINDERS
        "alert", "signin" -> CHANNEL_ALERTS
        else -> CHANNEL_CHECKINS          // bod, eod, checkin, weekly, meal, fact_check, unknown
    }

    /** Reminders and rest days belong with the day's plan; sign-in alerts too (Today lists who needs signing in). */
    fun routeFor(kind: String): String = when (kind) {
        "reminder", "rest", "signin" -> Routes.TODAY
        else -> Routes.CHAT
    }

    /** Anything Savvy says to start a conversation can be answered from the shade; system alerts can't. */
    fun allowsReply(kind: String): Boolean = kind != "alert" && kind != "signin"
}

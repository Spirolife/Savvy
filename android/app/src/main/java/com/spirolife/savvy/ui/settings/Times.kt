package com.spirolife.savvy.ui.settings

import java.util.Locale

/** "HH:MM" from the server as (hour, minute); falls back to 9:00 for anything odd. */
internal fun parseTime(value: String): Pair<Int, Int> {
    val parts = value.split(":")
    val h = parts.getOrNull(0)?.toIntOrNull()?.takeIf { it in 0..23 } ?: return 9 to 0
    val m = parts.getOrNull(1)?.toIntOrNull()?.takeIf { it in 0..59 } ?: 0
    return h to m
}

/** The wire form, zero-padded: "08:30". */
internal fun wireTime(hour: Int, minute: Int): String = String.format(Locale.ROOT, "%02d:%02d", hour, minute)

/** How the design shows a time: "8:30", "22:00". */
internal fun displayTime(value: String): String {
    val (h, m) = parseTime(value)
    return String.format(Locale.ROOT, "%d:%02d", h, m)
}

/** Quiet hours are whole hours on the server. */
internal fun displayHour(hour: Int): String = "$hour:00"

internal val WEEK_DAYS = listOf("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

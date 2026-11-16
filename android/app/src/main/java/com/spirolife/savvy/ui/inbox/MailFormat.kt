package com.spirolife.savvy.ui.inbox

import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit

/** "Ana Ruiz <ana@x.com>" -> "Ana Ruiz"; a bare address stays as is. */
fun senderName(from: String): String {
    val name = from.substringBefore('<').trim().trim('"')
    return name.ifEmpty { from.trim().trim('<', '>') }
}

fun accountLabel(account: String) = account.replaceFirstChar { it.uppercase() }

/** Time today, weekday this week, otherwise "Nov 3". */
fun shortTime(iso: String, now: OffsetDateTime = OffsetDateTime.now()): String {
    val t = runCatching { OffsetDateTime.parse(iso).atZoneSameInstant(ZoneId.systemDefault()) }.getOrNull() ?: return iso
    val today = now.atZoneSameInstant(ZoneId.systemDefault()).toLocalDate()
    val days = ChronoUnit.DAYS.between(t.toLocalDate(), today)
    return when {
        days == 0L -> t.format(DateTimeFormatter.ofPattern("h:mm a"))
        days in 1..6 -> t.format(DateTimeFormatter.ofPattern("EEE"))
        else -> t.format(DateTimeFormatter.ofPattern("MMM d"))
    }
}

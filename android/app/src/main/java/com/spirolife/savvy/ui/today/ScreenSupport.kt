package com.spirolife.savvy.ui.today

import android.text.format.DateFormat
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import com.spirolife.savvy.data.api.ApiException
import java.io.IOException
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.coroutines.cancellation.CancellationException

/*
 * Small helpers shared by the Today, Plan and Diary screens.
 */

/** What to tell the person when a call fails. */
internal fun friendlyError(e: Throwable): String = when (e) {
    is ApiException -> e.message.ifBlank { "The PC said no (${e.code})." }
    is IOException -> "Can't reach your PC"
    else -> e.message?.takeIf { it.isNotBlank() } ?: "Something went wrong"
}

/** Runs [block], keeping coroutine cancellation intact (runCatching would swallow it). */
internal suspend fun <T> attempt(block: suspend () -> T): Result<T> = try {
    Result.success(block())
} catch (e: CancellationException) {
    throw e
} catch (e: Exception) {
    Result.failure(e)
}

internal fun parseTime(iso: String): OffsetDateTime? =
    runCatching { OffsetDateTime.parse(iso) }.getOrNull()

/** "10:00" or "10:00 AM", following the phone's 24-hour setting, in the phone's zone. */
@Composable
internal fun rememberTimeFormatter(): (String) -> String {
    val context = LocalContext.current
    val is24 = DateFormat.is24HourFormat(context)
    return remember(is24) {
        val f = DateTimeFormatter.ofPattern(if (is24) "HH:mm" else "h:mm a", Locale.getDefault())
        val fn: (String) -> String = { iso ->
            parseTime(iso)?.atZoneSameInstant(ZoneId.systemDefault())?.format(f) ?: ""
        }
        fn
    }
}

/** "Monday, November 16". */
internal fun longDate(date: String): String = runCatching {
    LocalDate.parse(date).format(DateTimeFormatter.ofPattern("EEEE, MMMM d", Locale.getDefault()))
}.getOrDefault(date)

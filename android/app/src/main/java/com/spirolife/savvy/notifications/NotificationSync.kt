package com.spirolife.savvy.notifications

import com.spirolife.savvy.data.api.AppNotification
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull

/** Where the sync keeps its progress. Production: AppPrefs plus a small SharedPreferences flag. */
interface SyncState {
    /** Highest notification id already shown (0 = none). */
    fun lastId(): Long
    suspend fun setLastId(id: Long)

    /**
     * Whether this pairing has skipped past the history that was on the PC before
     * it. Without this a freshly paired phone would post weeks of old check-ins.
     */
    fun baselined(): Boolean
    fun setBaselined()
}

/**
 * Brings the phone up to date with GET /v1/notifications and posts what's new,
 * exactly once, whichever of polling or an FCM push sees it first.
 *
 * Both paths run in the same process, so one lock serialises them, and the
 * watermark ([SyncState.lastId]) only moves forward.
 */
class NotificationSync(
    private val fetch: suspend (afterId: Long, limit: Int) -> List<AppNotification>,
    private val state: SyncState,
    private val post: (AppNotification) -> Unit,
    private val pageSize: Int = 50,
) {
    private val lock = Mutex()

    /** Polls the feed; returns how many notifications were posted. Throws on network errors. */
    suspend fun sync(): Int = lock.withLock {
        if (!state.baselined()) {
            skipHistory()
            return@withLock 0
        }
        var last = state.lastId()
        var posted = 0
        while (true) {
            val page = fetch(last, pageSize)
            for (n in page.sortedBy { it.id }) {
                if (n.id <= last) continue
                post(n)
                posted++
                last = n.id
                state.setLastId(last)
            }
            if (page.size < pageSize) break
        }
        posted
    }

    /**
     * An FCM push arrived. Syncing (rather than posting the payload) also picks
     * up anything an earlier, lost push carried, and the full text of a body the
     * server had to clip. If the PC can't be reached, the payload itself is shown.
     */
    suspend fun onPush(pushed: AppNotification, timeoutMs: Long = 6_000) {
        val synced = try {
            withTimeoutOrNull(timeoutMs) { sync() }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            null
        }
        if (synced != null) return
        // The PC didn't answer: show what the push carried.
        lock.withLock {
            if (pushed.id > state.lastId()) {
                post(pushed)
                state.setLastId(pushed.id)
            }
        }
    }

    private suspend fun skipHistory() {
        var last = state.lastId()
        while (true) {
            val page = fetch(last, pageSize)
            page.maxOfOrNull { it.id }?.let { if (it > last) last = it }
            if (page.size < pageSize) break
        }
        if (last > state.lastId()) state.setLastId(last)
        state.setBaselined()
    }
}

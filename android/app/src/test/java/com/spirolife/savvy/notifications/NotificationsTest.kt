package com.spirolife.savvy.notifications

import com.spirolife.savvy.data.api.AppNotification
import com.spirolife.savvy.data.api.ChatEvent
import com.spirolife.savvy.ui.nav.Routes
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

class KindMappingTest {
    @Test fun channels() {
        for (k in listOf("bod", "eod", "checkin", "weekly", "meal", "fact_check", "something_new")) {
            assertEquals(k, NotificationKinds.CHANNEL_CHECKINS, NotificationKinds.channelFor(k))
        }
        assertEquals(NotificationKinds.CHANNEL_REMINDERS, NotificationKinds.channelFor("reminder"))
        assertEquals(NotificationKinds.CHANNEL_REMINDERS, NotificationKinds.channelFor("rest"))
        assertEquals(NotificationKinds.CHANNEL_ALERTS, NotificationKinds.channelFor("alert"))
        assertEquals(NotificationKinds.CHANNEL_ALERTS, NotificationKinds.channelFor("signin"))
    }

    @Test fun routes() {
        assertEquals(Routes.CHAT, NotificationKinds.routeFor("checkin"))
        assertEquals(Routes.CHAT, NotificationKinds.routeFor("weekly"))
        assertEquals(Routes.TODAY, NotificationKinds.routeFor("reminder"))
        assertEquals(Routes.TODAY, NotificationKinds.routeFor("rest"))
    }

    @Test fun replies() {
        assertTrue(NotificationKinds.allowsReply("checkin"))
        assertTrue(NotificationKinds.allowsReply("bod"))
        assertFalse(NotificationKinds.allowsReply("alert"))
        assertFalse(NotificationKinds.allowsReply("signin"))
    }

    @Test fun pushPayloadParses() {
        val n = SavvyMessagingService.parse(mapOf("id" to "42", "kind" to "meal", "title" to "Meal plan", "body" to "b"))!!
        assertEquals(42L, n.id)
        assertEquals("meal", n.kind)
        assertEquals(null, SavvyMessagingService.parse(mapOf("title" to "no id")))
    }
}

/** A fake PC feed and in-memory progress. */
private class Fake(ids: List<Long> = emptyList(), baselined: Boolean = true) : SyncState {
    val feed = ids.map { note(it) }.toMutableList()
    val posted = mutableListOf<Long>()
    var fail = false
    var hang = false
    var calls = 0
    private var last = 0L
    private var base = baselined

    suspend fun fetch(after: Long, limit: Int): List<AppNotification> {
        calls++
        if (hang) awaitCancellation()
        if (fail) throw IOException("PC asleep")
        return feed.filter { it.id > after }.sortedBy { it.id }.take(limit)
    }

    fun sync(pageSize: Int = 50) = NotificationSync(::fetch, this, { posted += it.id }, pageSize)

    override fun lastId() = last
    override suspend fun setLastId(id: Long) { last = id }
    override fun baselined() = base
    override fun setBaselined() { base = true }
}

private fun note(id: Long, body: String = "body $id") = AppNotification(id, "2026-11-16T10:00:00-05:00", "checkin", "t$id", body)

class NotificationSyncTest {
    @Test fun postsNewOnesInOrderAcrossPages() = runTest {
        val f = Fake((1L..7L).toList())
        val sync = f.sync(pageSize = 3)
        assertEquals(7, sync.sync())
        assertEquals((1L..7L).toList(), f.posted)
        assertEquals(7L, f.lastId())
        assertEquals(0, sync.sync())           // nothing twice
        f.feed += note(8)
        sync.sync()
        assertEquals((1L..8L).toList(), f.posted)
    }

    @Test fun firstSyncAfterPairingSkipsTheBacklog() = runTest {
        val f = Fake((1L..120L).toList(), baselined = false)
        val sync = f.sync()
        assertEquals(0, sync.sync())
        assertTrue(f.posted.isEmpty())
        assertEquals(120L, f.lastId())
        f.feed += note(121)
        sync.sync()
        assertEquals(listOf(121L), f.posted)
    }

    @Test fun emptyFeedAtPairingStillCountsAsBaselined() = runTest {
        val f = Fake(baselined = false)
        val sync = f.sync()
        sync.sync()
        f.feed += note(1)
        sync.sync()
        assertEquals(listOf(1L), f.posted)    // the PC's very first notification isn't swallowed
    }

    @Test fun pushThenPollShowsItOnce() = runTest {
        val f = Fake(listOf(1, 2))
        val sync = f.sync()
        sync.onPush(note(2))                  // the sync also catches #1, which had no push
        assertEquals(listOf(1L, 2L), f.posted)
        sync.sync()
        assertEquals(listOf(1L, 2L), f.posted)
    }

    @Test fun pushFallsBackToItsPayloadWhenThePcIsUnreachable() = runTest {
        val f = Fake(listOf(5)).apply { fail = true }
        val sync = f.sync()
        sync.onPush(note(5))
        sync.onPush(note(5))                  // FCM can deliver twice
        assertEquals(listOf(5L), f.posted)
        f.fail = false
        sync.sync()
        assertEquals(listOf(5L), f.posted)
    }

    @Test fun pushFallsBackWhenThePcHangs() = runTest {
        val f = Fake(listOf(9)).apply { hang = true }
        f.sync().onPush(note(9), timeoutMs = 100)
        assertEquals(listOf(9L), f.posted)
    }

    @Test fun pollFailurePropagatesWithoutMovingTheWatermark() = runTest {
        val f = Fake(listOf(1)).apply { fail = true }
        val result = runCatching { f.sync().sync() }
        assertTrue(result.exceptionOrNull() is IOException)
        assertEquals(0L, f.lastId())
    }
}

class ReplyTest {
    @Test fun collectsTheFinalReply() = runTest {
        val reply = ReplyWorker.collectReply(flowOf(ChatEvent.Text("Hel"), ChatEvent.Text("lo"), ChatEvent.Done("Hello!"))) {}
        assertEquals("Hello!", reply)
    }

    @Test fun declinesConfirmationsAndSaysSo() = runTest {
        val declined = mutableListOf<String>()
        val reply = ReplyWorker.collectReply(flowOf(
            ChatEvent.Confirm("c1", "send_email", 2, JsonObject(emptyMap())),
            ChatEvent.Done("I didn't send it."),
        )) { declined += it }
        assertEquals(listOf("c1"), declined)
        assertTrue(reply.startsWith("I didn't send it."))
        assertTrue(reply.contains("send_email"))
    }

    @Test fun errorEventFails() = runTest {
        val r = runCatching { ReplyWorker.collectReply(flowOf(ChatEvent.Error("busy"))) {} }
        assertEquals("busy", r.exceptionOrNull()?.message)
    }
}

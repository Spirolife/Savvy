package com.spirolife.savvy.ui.inbox

import com.spirolife.savvy.data.api.ApiException
import com.spirolife.savvy.data.api.Inbox
import com.spirolife.savvy.data.api.Mail
import com.spirolife.savvy.data.api.MailThread
import com.spirolife.savvy.data.api.Ok
import com.spirolife.savvy.data.api.ThreadMessage
import com.spirolife.savvy.ui.chat.ChatInboxFakeApi
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class InboxViewModelTest {
    private val dispatcher = StandardTestDispatcher()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private val a = Mail("m1", "t1", "personal", "Ana <ana@x.com>", "Lunch?", unread = true)
    private val b = Mail("m2", "t2", "school", "Dept", "Notice")

    private inner class Api : ChatInboxFakeApi() {
        val calls = mutableListOf<String>()
        val actions = mutableListOf<Triple<String, String, String>>()
        var gate: CompletableDeferred<Unit>? = null
        var failActions = false
        override suspend fun inbox(account: String, query: String?, limit: Int): Inbox {
            calls += "$account|${query.orEmpty()}"
            val msgs = listOf(a, b).filter { account == "all" || it.account == account }
            return Inbox("Ana wants lunch.", mapOf("personal" to 1, "school" to 0), msgs)
        }
        override suspend fun mailAction(account: String, messageId: String, action: String): Ok {
            actions += Triple(account, messageId, action)
            gate?.await()
            if (failActions) throw ApiException(502, "action_failed", "Gmail didn't accept that change.")
            return Ok()
        }
    }

    private fun TestScope.loaded(api: Api): InboxViewModel = InboxViewModel(api).also { advanceUntilIdle() }

    @Test fun loadsSummaryCountsAndAccounts() = runTest(dispatcher) {
        val vm = loaded(Api())
        val s = vm.state.value
        assertEquals("Ana wants lunch.", s.summary)
        assertEquals(listOf("personal", "school"), s.accounts)
        assertEquals(listOf("m1", "m2"), s.messages.map { it.id })
        assertFalse(s.loading)
    }

    @Test fun accountFilterKeepsChips() = runTest(dispatcher) {
        val api = Api()
        val vm = loaded(api)
        vm.selectAccount("school"); advanceUntilIdle()
        assertEquals(listOf("m2"), vm.state.value.messages.map { it.id })
        assertEquals(listOf("personal", "school"), vm.state.value.accounts)
        assertEquals("school|", api.calls.last())
    }

    @Test fun searchIsDebounced() = runTest(dispatcher) {
        val api = Api()
        val vm = loaded(api)
        vm.setQuery("lu"); advanceTimeBy(100)
        vm.setQuery("lunch"); advanceUntilIdle()
        assertEquals(listOf("all|", "all|lunch"), api.calls)
    }

    @Test fun optimisticArchiveRollsBackOnFailure() = runTest(dispatcher) {
        val api = Api().apply { gate = CompletableDeferred(); failActions = true }
        val vm = loaded(api)
        vm.act(a, "archive"); dispatcher.scheduler.runCurrent()
        // Gone at once, and the unread count follows.
        assertEquals(listOf("m2"), vm.state.value.messages.map { it.id })
        assertEquals(0, vm.state.value.unread["personal"])
        api.gate!!.complete(Unit); advanceUntilIdle()
        assertEquals(listOf("m1", "m2"), vm.state.value.messages.map { it.id })
        assertEquals(1, vm.state.value.unread["personal"])
        assertTrue(vm.state.value.notice!!.text.startsWith("Couldn't archive"))
        assertNull(vm.state.value.notice!!.undo)
    }

    @Test fun optimisticStarRollsBackOnFailure() = runTest(dispatcher) {
        val api = Api().apply { failActions = true }
        val vm = loaded(api)
        vm.act(b, "star"); advanceUntilIdle()
        assertFalse(vm.state.value.messages.first { it.id == "m2" }.starred)
    }

    @Test fun readCanBeUndone() = runTest(dispatcher) {
        val api = Api()
        val vm = loaded(api)
        vm.act(a, "read"); advanceUntilIdle()
        assertFalse(vm.state.value.messages.first { it.id == "m1" }.unread)
        val notice = vm.state.value.notice!!
        assertEquals("unread", notice.undo)
        vm.noticeShown(notice)
        vm.undo(notice); advanceUntilIdle()
        assertTrue(vm.state.value.messages.first { it.id == "m1" }.unread)
        assertEquals(listOf("read", "unread"), api.actions.map { it.third })
        assertEquals("personal", api.actions.last().first)
        assertNull(vm.state.value.notice)     // the undo doesn't offer another undo
    }

    @Test fun archiveHasNoUndo() = runTest(dispatcher) {
        val vm = loaded(Api())
        vm.act(a, "archive"); advanceUntilIdle()
        assertEquals("Archived", vm.state.value.notice!!.text)
        assertNull(vm.state.value.notice!!.undo)
    }

    @Test fun threadArchiveActsOnEveryMessage() = runTest(dispatcher) {
        val api = object : ChatInboxFakeApi() {
            val actions = mutableListOf<String>()
            override suspend fun thread(account: String, threadId: String) =
                MailThread("Lunch?", listOf(ThreadMessage("x1", "Ana <a@x>"), ThreadMessage("x2", "Me")))
            override suspend fun mailAction(account: String, messageId: String, action: String): Ok {
                actions += "$messageId:$action"; return Ok()
            }
        }
        val vm = ThreadViewModel(api, "personal", "t1"); advanceUntilIdle()
        vm.archive(); advanceUntilIdle()
        assertTrue(vm.state.value.archived)
        assertEquals(listOf("x2:read", "x1:archive", "x2:archive"), api.actions)
        assertTrue(replyPrompt("personal", vm.state.value.thread!!).startsWith("Draft a reply to Me's email \"Lunch?\""))
    }

    @Test fun formatsSenders() {
        assertEquals("Ana Ruiz", senderName("\"Ana Ruiz\" <ana@x.com>"))
        assertEquals("ana@x.com", senderName("<ana@x.com>"))
    }
}

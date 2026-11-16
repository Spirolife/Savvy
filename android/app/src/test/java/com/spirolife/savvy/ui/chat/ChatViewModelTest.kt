package com.spirolife.savvy.ui.chat

import com.spirolife.savvy.data.api.ApiException
import com.spirolife.savvy.data.api.ChatEvent
import com.spirolife.savvy.data.api.ChatMessage
import com.spirolife.savvy.data.api.Ok
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ChatViewModelTest {
    @Before fun setUp() = Dispatchers.setMain(UnconfinedTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    private val args = buildJsonObject { put("to", "a@example.com"); put("subject", "Hi"); put("body", "Hello there") }

    @Test fun historyLoadsOnOpen() = runTest {
        val vm = ChatViewModel(object : ChatInboxFakeApi() {
            override suspend fun chatHistory(limit: Int) = listOf(
                ChatMessage("user", "hi", surface = "signal"), ChatMessage("assistant", "hello", surface = "app"))
        })
        val items = vm.state.value.items
        assertEquals(listOf("hi", "hello"), items.map { (it as? ChatItem.User)?.text ?: (it as ChatItem.Assistant).text })
        assertFalse(vm.state.value.loadingHistory)
    }

    @Test fun streamingAccumulatesTextAndTools() = runTest {
        val vm = ChatViewModel(object : ChatInboxFakeApi() {
            override fun chat(message: String): Flow<ChatEvent> = flow {
                emit(ChatEvent.Text("Let me "))
                emit(ChatEvent.Text("check."))
                emit(ChatEvent.Tool("find_event", "read", "1 item(s)", null))
                emit(ChatEvent.Text("Moved it."))
                emit(ChatEvent.Done("Moved it.\n\n[Sign in](https://x)"))
            }
        })
        vm.setDraft("  move my 3:30  ")
        vm.send()
        val s = vm.state.value
        assertFalse(s.running)
        assertEquals("", s.draft)
        val items = s.items
        assertEquals(4, items.size)
        assertEquals("move my 3:30", (items[0] as ChatItem.User).text)
        assertEquals("Let me check.", (items[1] as ChatItem.Assistant).text)
        assertEquals("read", (items[2] as ChatItem.ToolCard).state)
        // The final reply carries the sign-in link that wasn't streamed.
        assertEquals("Moved it.\n\n[Sign in](https://x)", (items[3] as ChatItem.Assistant).text)
        assertTrue(items.none { it is ChatItem.Assistant && it.streaming })
    }

    @Test fun doneWithoutStreamingAddsReply() = runTest {
        val vm = ChatViewModel(object : ChatInboxFakeApi() {
            override fun chat(message: String) = flow { emit(ChatEvent.Done("Signed in.")) }
        })
        vm.setDraft("http://localhost/?code=x"); vm.send()
        assertEquals("Signed in.", (vm.state.value.items.last() as ChatItem.Assistant).text)
    }

    @Test fun confirmCallsApiWithIdAndDecision() = runTest {
        for (allow in listOf(true, false)) {
            val answered = CompletableDeferred<Pair<String, Boolean>>()
            val vm = ChatViewModel(object : ChatInboxFakeApi() {
                override fun chat(message: String) = flow {
                    emit(ChatEvent.Confirm("c7", "send_email", 2, args))
                    val (_, ok) = answered.await()
                    emit(ChatEvent.Tool("send_email", if (ok) "done" else "denied", "", null))
                    emit(ChatEvent.Done(if (ok) "Sent." else "Not sent."))
                }
                override suspend fun confirm(id: String, allow: Boolean): Ok {
                    answered.complete(id to allow); return Ok()
                }
            })
            vm.setDraft("email them"); vm.send()
            assertEquals("c7", vm.state.value.confirm?.id)
            assertTrue(vm.state.value.running)
            vm.answer(allow)
            assertEquals("c7" to allow, answered.await())
            val s = vm.state.value
            assertNull(s.confirm)
            assertFalse(s.running)
            assertEquals(if (allow) "Sent." else "Not sent.", (s.items.last() as ChatItem.Assistant).text)
        }
    }

    @Test fun errorKeepsTextForRetry() = runTest {
        var calls = 0
        val vm = ChatViewModel(object : ChatInboxFakeApi() {
            override fun chat(message: String) = flow {
                calls++
                if (calls == 1) throw ApiException(409, "busy", "busy") else emit(ChatEvent.Done("ok"))
            }
        })
        vm.setDraft("hello"); vm.send()
        assertEquals("hello", vm.state.value.retryText)
        assertTrue(vm.state.value.error!!.contains("still answering"))
        vm.retry()
        assertNull(vm.state.value.error)
        assertEquals(listOf("hello", "ok"), vm.state.value.items.map {
            (it as? ChatItem.User)?.text ?: (it as ChatItem.Assistant).text })
    }

    @Test fun errorEventShowsMessage() = runTest {
        val vm = ChatViewModel(object : ChatInboxFakeApi() {
            override fun chat(message: String) = flow { emit(ChatEvent.Text("par")); emit(ChatEvent.Error("model down")) }
        })
        vm.setDraft("hi"); vm.send()
        assertEquals("model down", vm.state.value.error)
        assertFalse(vm.state.value.running)
    }

    @Test fun confirmCopyReadsLikeAnEmail() {
        val copy = confirmCopy("send_email", 2, args)
        assertEquals("Send this email?", copy.title)
        assertEquals(listOf("To" to "a@example.com", "Subject" to "Hi"), copy.fields)
        assertEquals("Hello there", copy.body)
        assertEquals("Don't send" to "Send", copy.denyLabel to copy.allowLabel)

        val other = confirmCopy("delete_calendar_event", 2, buildJsonObject { put("event_id", JsonPrimitive("e1")) })
        assertEquals(listOf("Event id" to "e1"), other.fields)
        assertEquals("Allow" , other.allowLabel)
    }

    @Test fun prefillIsTakenOnce() {
        ChatPrefill.set("Draft a reply")
        assertEquals("Draft a reply", ChatPrefill.take())
        assertNull(ChatPrefill.take())
    }
}

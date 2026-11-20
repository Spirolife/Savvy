package com.spirolife.savvy.ui.chat

import androidx.lifecycle.ViewModel
import java.io.IOException
import androidx.lifecycle.viewModelScope
import com.spirolife.savvy.data.api.ApiException
import com.spirolife.savvy.data.api.ChatEvent
import com.spirolife.savvy.data.api.SavvyApi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** One row of the conversation. */
sealed interface ChatItem {
    val key: String

    data class User(override val key: String, val text: String) : ChatItem
    data class Assistant(override val key: String, val text: String, val streaming: Boolean = false) : ChatItem
    data class ToolCard(
        override val key: String, val name: String, val state: String,
        val detail: String, val warning: String?,
    ) : ChatItem
}

data class ChatUiState(
    val items: List<ChatItem> = emptyList(),
    val loadingHistory: Boolean = true,
    val historyError: String? = null,
    val running: Boolean = false,
    /** The stream dropped (the app went to the background, say); catching up with the PC. */
    val reconnecting: Boolean = false,
    val confirm: ChatEvent.Confirm? = null,
    /** The last turn failed; [retryText] can be sent again. */
    val error: String? = null,
    val retryText: String? = null,
    val draft: String = "",
)

/**
 * Chat: history, one streamed turn at a time, confirmations. The turn runs in
 * [viewModelScope], so leaving the Chat tab doesn't cancel it.
 *
 * Android cuts the stream when the app goes to the background, but the turn
 * carries on on the PC. So once the turn has started, a dropped stream isn't an
 * error: the app polls the turn's events every [catchUpDelayMs] until it ends,
 * and gives up only after [maxCatchUpFailures] failed polls in a row.
 */
class ChatViewModel(
    private val api: SavvyApi,
    private val catchUpDelayMs: Long = 3_000,
    private val maxCatchUpFailures: Int = 200,     // ~10 minutes at 3 s
) : ViewModel() {
    private val _state = MutableStateFlow(ChatUiState())
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    private var nextKey = 0
    private var turn: Job? = null

    init {
        loadHistory()
    }

    private fun key(prefix: String) = "$prefix${nextKey++}"

    fun loadHistory() {
        _state.update { it.copy(loadingHistory = true, historyError = null) }
        viewModelScope.launch {
            try {
                val history = api.chatHistory(40)
                val past = history.map {
                    if (it.role == "user") ChatItem.User(key("h"), it.content)
                    else ChatItem.Assistant(key("h"), it.content)
                }
                // Anything sent while history loaded stays after it.
                _state.update { it.copy(items = past + it.items, loadingHistory = false) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(loadingHistory = false, historyError = friendly(e)) }
            }
        }
    }

    fun setDraft(text: String) = _state.update { it.copy(draft = text) }

    /** Sends the draft. Does nothing while a turn runs or when it's blank. */
    fun send() {
        val text = _state.value.draft.trim()
        if (text.isEmpty() || turn?.isActive == true) return
        _state.update { it.copy(draft = "") }
        start(text)
    }

    fun retry() {
        val text = _state.value.retryText ?: return
        if (turn?.isActive == true) return
        start(text, echo = false)
    }

    fun dismissError() = _state.update { it.copy(error = null, retryText = null) }

    private fun start(text: String, echo: Boolean = true) {
        _state.update {
            it.copy(
                items = if (echo) it.items + ChatItem.User(key("u"), text) else it.items,
                running = true, error = null, retryText = null,
            )
        }
        turn = viewModelScope.launch {
            val t = TurnProgress(text)
            try {
                api.chat(text).collect { event ->
                    if (event is ChatEvent.Start) t.id = event.id else { t.received++; handle(event, t) }
                }
                if (!t.finished) catchUpOrFail(t, IOException("The connection to your PC closed before Savvy finished."))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                catchUpOrFail(t, e)
            }
        }
    }

    /** One turn as the app has seen it so far. */
    private class TurnProgress(val text: String) {
        var id: String? = null
        var received = 0            // events after Start, which is what the server counts
        var streamed = false
        var finished = false
    }

    private fun handle(event: ChatEvent, t: TurnProgress) {
        when (event) {
            is ChatEvent.Start -> {}
            is ChatEvent.Text -> { t.streamed = true; appendText(event.delta) }
            is ChatEvent.Tool -> addTool(event)
            is ChatEvent.Confirm -> _state.update { it.copy(confirm = event) }
            is ChatEvent.Done -> { t.finished = true; finish(event.reply, t.streamed) }
            is ChatEvent.Error -> { t.finished = true; fail(event.message, t.text) }
        }
    }

    /**
     * The stream ended early. A refusal from the server (busy, unpaired) or a
     * stream that never started is a real failure; otherwise the turn is still
     * running on the PC, so fetch what was missed until it ends.
     */
    private suspend fun catchUpOrFail(t: TurnProgress, cause: Exception) {
        val id = t.id
        if (id == null || cause is ApiException) return fail(friendly(cause), t.text)
        _state.update { it.copy(reconnecting = true) }
        var failures = 0
        while (!t.finished) {
            delay(catchUpDelayMs)
            try {
                val got = api.chatTurnEvents(id, t.received)
                failures = 0
                _state.update { it.copy(reconnecting = false) }
                t.received = got.next
                got.events.forEach { handle(it, t) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                if (e.status == 404) return fail("Your PC lost that answer (Savvy restarted?). Send it again.", t.text)
                if (++failures >= maxCatchUpFailures) return fail(friendly(e), t.text)
            } catch (e: Exception) {
                _state.update { it.copy(reconnecting = true) }
                if (++failures >= maxCatchUpFailures) return fail(friendly(e), t.text)
            }
        }
    }

    private fun appendText(delta: String) = _state.update { s ->
        val last = s.items.lastOrNull()
        val items = if (last is ChatItem.Assistant && last.streaming) {
            s.items.dropLast(1) + last.copy(text = last.text + delta)
        } else {
            s.items + ChatItem.Assistant(key("a"), delta, streaming = true)
        }
        s.copy(items = items)
    }

    private fun addTool(e: ChatEvent.Tool) = _state.update { s ->
        s.copy(items = endStreaming(s.items) + ChatItem.ToolCard(key("t"), e.name, e.state, e.detail, e.warning))
    }

    private fun finish(reply: String, streamed: Boolean) = _state.update { s ->
        var items = endStreaming(s.items)
        val lastIndex = items.indexOfLast { it is ChatItem.User }
        val lastAssistant = items.withIndex().lastOrNull { (i, it) -> i > lastIndex && it is ChatItem.Assistant }
        if (reply.isNotBlank()) {
            if (!streamed || lastAssistant == null) {
                items = items + ChatItem.Assistant(key("a"), reply)
            } else {
                // The final reply can carry more than was streamed (a sign-in link).
                val a = lastAssistant.value as ChatItem.Assistant
                if (reply.length > a.text.length && reply.trimStart().startsWith(a.text.trim())) {
                    items = items.toMutableList().also { it[lastAssistant.index] = a.copy(text = reply) }
                }
            }
        }
        s.copy(items = items, running = false, reconnecting = false, confirm = null)
    }

    private fun fail(message: String, text: String) = _state.update { s ->
        s.copy(items = endStreaming(s.items), running = false, reconnecting = false, confirm = null,
            error = message, retryText = text)
    }

    private fun endStreaming(items: List<ChatItem>): List<ChatItem> {
        val last = items.lastOrNull()
        return if (last is ChatItem.Assistant && last.streaming) items.dropLast(1) + last.copy(streaming = false) else items
    }

    /** Answer the open confirmation; the sheet closes at once. */
    fun answer(allow: Boolean) {
        val c = _state.value.confirm ?: return
        _state.update { it.copy(confirm = null) }
        viewModelScope.launch {
            try {
                api.confirm(c.id, allow)
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                if (e.status != 404) reopen(c, friendly(e))     // 404: it already timed out
            } catch (e: Exception) {
                reopen(c, friendly(e))
            }
        }
    }

    /** The answer didn't reach the PC, which is still waiting: show the sheet again. */
    private fun reopen(c: ChatEvent.Confirm, message: String) = _state.update {
        it.copy(error = message, confirm = if (it.running && it.confirm == null) c else it.confirm)
    }

    private fun friendly(e: Exception): String = when {
        e is ApiException && e.code == "busy" -> "Savvy is still answering another message. Try again in a moment."
        e is ApiException -> e.message
        else -> "Couldn't reach Savvy on your PC. Check that it's on and connected."
    }
}

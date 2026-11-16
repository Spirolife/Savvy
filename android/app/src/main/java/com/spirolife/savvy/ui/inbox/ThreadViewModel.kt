package com.spirolife.savvy.ui.inbox

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.spirolife.savvy.data.api.MailThread
import com.spirolife.savvy.data.api.SavvyApi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ThreadUiState(
    val loading: Boolean = true,
    val error: String? = null,
    val thread: MailThread? = null,
    val starred: Boolean = false,
    val busy: Boolean = false,
    val notice: String? = null,
    /** Archived: the screen goes back to the inbox. */
    val archived: Boolean = false,
)

class ThreadViewModel(private val api: SavvyApi, val account: String, val threadId: String) : ViewModel() {
    private val _state = MutableStateFlow(ThreadUiState())
    val state: StateFlow<ThreadUiState> = _state.asStateFlow()

    init {
        load()
    }

    fun load() {
        _state.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            try {
                val t = api.thread(account, threadId)
                _state.update { it.copy(loading = false, thread = t) }
                // Opening a thread reads it, as in Gmail.
                t.messages.lastOrNull()?.let { runCatching { api.mailAction(account, it.id, "read") } }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(loading = false, error = friendly(e)) }
            }
        }
    }

    /** Archive every message, so the whole conversation leaves the inbox. */
    fun archive() = run("archive", all = true) { it.copy(archived = true) }

    fun toggleStar() {
        val action = if (_state.value.starred) "unstar" else "star"
        run(action) { it.copy(starred = action == "star", notice = if (action == "star") "Starred" else "Unstarred") }
    }

    fun markUnread() = run("unread") { it.copy(notice = "Marked unread") }

    fun noticeShown() = _state.update { it.copy(notice = null) }

    private fun run(action: String, all: Boolean = false, after: (ThreadUiState) -> ThreadUiState) {
        val messages = _state.value.thread?.messages.orEmpty()
        if (messages.isEmpty() || _state.value.busy) return
        _state.update { it.copy(busy = true) }
        viewModelScope.launch {
            try {
                val targets = if (all) messages else listOf(messages.last())
                targets.forEach { api.mailAction(account, it.id, action) }
                _state.update { after(it).copy(busy = false) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(busy = false, notice = "That didn't work. ${friendly(e)}") }
            }
        }
    }
}

/** The text "Ask Savvy to reply" leaves in the chat box. */
fun replyPrompt(account: String, thread: MailThread): String {
    val last = thread.messages.lastOrNull()
    val from = last?.from?.let(::senderName)?.takeIf { it.isNotBlank() } ?: "the sender"
    return "Draft a reply to $from's email \"${thread.subject}\" (my $account account, " +
        "message ${last?.id.orEmpty()}). Show me the draft before sending. "
}

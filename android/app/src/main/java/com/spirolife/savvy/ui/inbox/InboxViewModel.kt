package com.spirolife.savvy.ui.inbox

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.spirolife.savvy.data.api.ApiException
import com.spirolife.savvy.data.api.Mail
import com.spirolife.savvy.data.api.SavvyApi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** A snackbar message; [undo] is the action that reverses it, when there is one. */
data class InboxNotice(val id: Long, val text: String, val mail: Mail? = null, val undo: String? = null)

data class InboxUiState(
    val loading: Boolean = true,
    val error: String? = null,
    val summary: String? = null,
    val unread: Map<String, Int> = emptyMap(),
    val messages: List<Mail> = emptyList(),
    /** Every account seen so far, so the chips don't vanish while one is selected. */
    val accounts: List<String> = emptyList(),
    val account: String = "all",
    val query: String = "",
    val notice: InboxNotice? = null,
)

/** The action that undoes [action], or null when the API has no way back. */
fun inverseOf(action: String): String? = when (action) {
    "star" -> "unstar"
    "unstar" -> "star"
    "read" -> "unread"
    "unread" -> "read"
    else -> null    // archive and trash: the API has no "move back to inbox"
}

class InboxViewModel(private val api: SavvyApi, private val searchDelayMs: Long = 400) : ViewModel() {
    private val _state = MutableStateFlow(InboxUiState())
    val state: StateFlow<InboxUiState> = _state.asStateFlow()

    private var loadJob: Job? = null
    private var searchJob: Job? = null
    private var noticeIds = 0L

    init {
        load()
    }

    fun load() {
        loadJob?.cancel()
        val s = _state.value
        _state.update { it.copy(loading = true, error = null) }
        loadJob = viewModelScope.launch {
            try {
                val inbox = api.inbox(s.account, s.query.trim().ifEmpty { null })
                _state.update {
                    val seen = (it.accounts + inbox.unread.keys + inbox.messages.map { m -> m.account }).distinct().sorted()
                    it.copy(loading = false, summary = inbox.summary, unread = inbox.unread,
                        messages = inbox.messages, accounts = seen)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(loading = false, error = friendly(e)) }
            }
        }
    }

    fun selectAccount(account: String) {
        if (account == _state.value.account) return
        _state.update { it.copy(account = account) }
        load()
    }

    /** Typing searches after a short pause; [searchNow] (the keyboard's search key) doesn't wait. */
    fun setQuery(query: String) {
        _state.update { it.copy(query = query) }
        searchJob?.cancel()
        searchJob = viewModelScope.launch {
            delay(searchDelayMs)
            load()
        }
    }

    fun searchNow() {
        searchJob?.cancel()
        load()
    }

    /**
     * archive, trash, star, unstar, read or unread. The list changes at once;
     * a failure puts the message back as it was and says so.
     */
    fun act(mail: Mail, action: String, offerUndo: Boolean = true) {
        val index = _state.value.messages.indexOfFirst { it.id == mail.id }
        val original = _state.value.messages.getOrNull(index) ?: mail
        _state.update { it.copy(messages = applied(it.messages, original, action), unread = unreadAfter(it.unread, original, action)) }
        viewModelScope.launch {
            try {
                api.mailAction(original.account, original.id, action)
                val undo = if (offerUndo) inverseOf(action) else null
                if (offerUndo) notify(doneText(action), applied(listOf(original), original, action).firstOrNull() ?: original, undo)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update {
                    it.copy(messages = restored(it.messages, original, index), unread = unreadAfter(it.unread, original, action, undo = true))
                }
                notify("Couldn't ${verb(action)} that message. ${friendly(e)}", null, null)
            }
        }
    }

    fun undo(notice: InboxNotice) {
        val mail = notice.mail ?: return
        val action = notice.undo ?: return
        act(mail, action, offerUndo = false)
    }

    fun noticeShown(notice: InboxNotice) = _state.update { if (it.notice?.id == notice.id) it.copy(notice = null) else it }

    private fun notify(text: String, mail: Mail?, undo: String?) =
        _state.update { it.copy(notice = InboxNotice(++noticeIds, text, mail, undo)) }

    private fun applied(list: List<Mail>, mail: Mail, action: String): List<Mail> = when (action) {
        "archive", "trash" -> list.filterNot { it.id == mail.id }
        else -> list.map { if (it.id == mail.id) changed(it, action) else it }
    }

    private fun changed(m: Mail, action: String) = when (action) {
        "star" -> m.copy(starred = true)
        "unstar" -> m.copy(starred = false)
        "read" -> m.copy(unread = false)
        "unread" -> m.copy(unread = true)
        else -> m
    }

    private fun restored(list: List<Mail>, original: Mail, index: Int): List<Mail> =
        if (list.any { it.id == original.id }) list.map { if (it.id == original.id) original else it }
        else list.toMutableList().apply { add(index.coerceIn(0, size), original) }

    private fun unreadAfter(unread: Map<String, Int>, m: Mail, action: String, undo: Boolean = false): Map<String, Int> {
        val delta = when {
            action == "read" && m.unread -> -1
            action == "unread" && !m.unread -> 1
            (action == "archive" || action == "trash") && m.unread -> -1
            else -> 0
        } * (if (undo) -1 else 1)
        val n = unread[m.account] ?: return unread
        return if (delta == 0) unread else unread + (m.account to (n + delta).coerceAtLeast(0))
    }

    private fun doneText(action: String) = when (action) {
        "archive" -> "Archived"
        "trash" -> "Moved to trash"
        "star" -> "Starred"
        "unstar" -> "Unstarred"
        "read" -> "Marked read"
        "unread" -> "Marked unread"
        else -> "Done"
    }

    private fun verb(action: String) = when (action) {
        "read" -> "mark read"
        "unread" -> "mark unread"
        else -> action
    }
}

internal fun friendly(e: Exception): String = when (e) {
    is ApiException -> e.message
    else -> "Couldn't reach Savvy on your PC."
}

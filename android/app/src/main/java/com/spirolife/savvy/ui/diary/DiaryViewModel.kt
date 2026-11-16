package com.spirolife.savvy.ui.diary

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.spirolife.savvy.Graph
import com.spirolife.savvy.data.api.DiaryEntry
import com.spirolife.savvy.data.api.Note
import com.spirolife.savvy.data.api.NoteSummary
import com.spirolife.savvy.data.api.SavvyApi
import com.spirolife.savvy.ui.today.attempt
import com.spirolife.savvy.ui.today.friendlyError
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.time.LocalTime

/** The writing prompt: the morning plan before noon, the evening reflection after. */
enum class DiaryPrompt(val type: String, val kicker: String, val question: String, val fieldLabel: String) {
    Morning("bod", "Morning plan", "What's the plan for today?", "Plan"),
    Evening("eod", "Evening reflection", "How did today go?", "Reflection"),
}

fun promptFor(time: LocalTime): DiaryPrompt = if (time.isBefore(LocalTime.NOON)) DiaryPrompt.Morning else DiaryPrompt.Evening

enum class DiaryTab { Entries, Notes }

data class NoteSheet(val name: String, val note: Note? = null, val error: String? = null)

data class DiaryUiState(
    val tab: DiaryTab = DiaryTab.Entries,
    val prompt: DiaryPrompt = DiaryPrompt.Morning,
    val draft: String = "",
    val saving: Boolean = false,
    val entries: List<DiaryEntry>? = null,
    val entriesError: String? = null,
    val notes: List<NoteSummary>? = null,
    val notesError: String? = null,
    val refreshing: Boolean = false,
    val sheet: NoteSheet? = null,
    val message: String? = null,
)

class DiaryViewModel(
    private val api: SavvyApi = Graph.api,
    private val clock: () -> LocalTime = { LocalTime.now() },
) : ViewModel() {
    private val _state = MutableStateFlow(DiaryUiState(prompt = promptFor(clock())))
    val state: StateFlow<DiaryUiState> = _state.asStateFlow()

    /** On resume: re-pick the prompt (the app may have slept past noon) and reload the open tab. */
    fun load() {
        _state.update { it.copy(prompt = promptFor(clock())) }
        reloadTab(pull = false)
    }

    fun refresh() = reloadTab(pull = true)

    private fun reloadTab(pull: Boolean) = when (_state.value.tab) {
        DiaryTab.Entries -> loadEntries(pull)
        DiaryTab.Notes -> loadNotes(pull)
    }

    fun selectTab(tab: DiaryTab) {
        _state.update { it.copy(tab = tab) }
        val s = _state.value
        if (tab == DiaryTab.Notes && s.notes == null) loadNotes(false)
        if (tab == DiaryTab.Entries && s.entries == null) loadEntries(false)
    }

    private fun loadEntries(pull: Boolean) {
        _state.update { it.copy(refreshing = pull, entriesError = null) }
        viewModelScope.launch {
            attempt { api.diary(14) }
                .onSuccess { list -> _state.update { it.copy(entries = list, refreshing = false) } }
                .onFailure { e -> _state.update { it.copy(entriesError = friendlyError(e), refreshing = false) } }
        }
    }

    private fun loadNotes(pull: Boolean) {
        _state.update { it.copy(refreshing = pull, notesError = null) }
        viewModelScope.launch {
            attempt { api.notes() }
                .onSuccess { list -> _state.update { it.copy(notes = list, refreshing = false) } }
                .onFailure { e -> _state.update { it.copy(notesError = friendlyError(e), refreshing = false) } }
        }
    }

    fun setDraft(text: String) = _state.update { it.copy(draft = text) }

    /** Save the draft as today's morning plan or evening reflection. */
    fun save() {
        val s = _state.value
        if (s.draft.isBlank()) return
        submit(s.prompt.type, s.draft.trim(), "Saved to your diary.")
    }

    /** Log a bad day: what's in the box, or a plain note when it's empty. */
    fun badDay() = submit("bad_day", _state.value.draft.trim().ifEmpty { "It was a bad day." }, "Logged. Go easy on yourself.")

    private fun submit(type: String, text: String, done: String) {
        if (_state.value.saving) return
        _state.update { it.copy(saving = true) }
        viewModelScope.launch {
            attempt { api.addDiary(type, text) }
                .onSuccess {
                    _state.update { it.copy(saving = false, draft = "", message = done) }
                    loadEntries(false)
                }
                .onFailure { e -> _state.update { it.copy(saving = false, message = friendlyError(e)) } }
        }
    }

    fun openNote(name: String) {
        _state.update { it.copy(sheet = NoteSheet(name)) }
        viewModelScope.launch {
            val result = attempt { api.note(name) }
            _state.update { s ->
                if (s.sheet?.name != name) s
                else s.copy(sheet = result.fold({ NoteSheet(name, note = it) }, { NoteSheet(name, error = friendlyError(it)) }))
            }
        }
    }

    fun closeNote() = _state.update { it.copy(sheet = null) }

    fun messageShown() = _state.update { it.copy(message = null) }
}

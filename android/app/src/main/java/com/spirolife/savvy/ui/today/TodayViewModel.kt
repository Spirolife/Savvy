package com.spirolife.savvy.ui.today

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.spirolife.savvy.Graph
import com.spirolife.savvy.data.api.SavvyApi
import com.spirolife.savvy.data.api.Task
import com.spirolife.savvy.data.api.Today
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class TodayUiState(
    val today: Today? = null,
    val loading: Boolean = true,
    val refreshing: Boolean = false,
    /** Load failed and there's nothing to show. */
    val error: String? = null,
    /** A one-off message (snackbar): a failed toggle, a booked rest day. */
    val message: String? = null,
    /** "Not now" hides the rest-day card until the app is restarted. */
    val restDismissed: Boolean = false,
    val planningRest: Boolean = false,
)

class TodayViewModel(private val api: SavvyApi = Graph.api) : ViewModel() {
    private val _state = MutableStateFlow(TodayUiState())
    val state: StateFlow<TodayUiState> = _state.asStateFlow()

    /** First load and resume: spinner only when there's nothing on screen yet. */
    fun load() = fetch(pullToRefresh = false)

    fun refresh() = fetch(pullToRefresh = true)

    private fun fetch(pullToRefresh: Boolean) {
        _state.update {
            it.copy(loading = it.today == null, refreshing = pullToRefresh && it.today != null)
        }
        viewModelScope.launch {
            attempt { api.today() }
                .onSuccess { t -> _state.update { it.copy(today = t, loading = false, refreshing = false, error = null) } }
                .onFailure { e ->
                    _state.update {
                        if (it.today == null) it.copy(loading = false, refreshing = false, error = friendlyError(e))
                        else it.copy(loading = false, refreshing = false, message = friendlyError(e))
                    }
                }
        }
    }

    /** Tick or untick a task right away; put it back if the PC says no. */
    fun setTaskDone(task: Task, done: Boolean) {
        if (task.completed == done) return
        setCompleted(task.id, done)
        viewModelScope.launch {
            attempt { if (done) api.completeTask(task.id) else api.reopenTask(task.id) }
                .onFailure { e ->
                    setCompleted(task.id, !done)
                    _state.update { it.copy(message = friendlyError(e)) }
                }
        }
    }

    private fun setCompleted(id: String, done: Boolean) = _state.update { s ->
        val t = s.today ?: return@update s
        s.copy(today = t.copy(tasks = t.tasks.map { if (it.id == id) it.copy(completed = done) else it }))
    }

    fun planRestDay() {
        val day = _state.value.today?.rest?.day ?: return
        _state.update { it.copy(planningRest = true) }
        viewModelScope.launch {
            attempt { api.planRestDay(day) }
                .onSuccess { ok ->
                    _state.update {
                        it.copy(planningRest = false, restDismissed = true,
                            message = ok.message ?: "Rest day planned.")
                    }
                }
                .onFailure { e -> _state.update { it.copy(planningRest = false, message = friendlyError(e)) } }
        }
    }

    fun dismissRest() = _state.update { it.copy(restDismissed = true) }

    fun messageShown() = _state.update { it.copy(message = null) }
}

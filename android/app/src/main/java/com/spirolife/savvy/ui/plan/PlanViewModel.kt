package com.spirolife.savvy.ui.plan

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.spirolife.savvy.Graph
import com.spirolife.savvy.data.api.Goal
import com.spirolife.savvy.data.api.NewMeal
import com.spirolife.savvy.data.api.Plan
import com.spirolife.savvy.data.api.Project
import com.spirolife.savvy.data.api.SavvyApi
import com.spirolife.savvy.data.api.Task
import com.spirolife.savvy.ui.today.attempt
import com.spirolife.savvy.ui.today.friendlyError
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** The bottom sheet of one Todoist project's open tasks. */
data class ProjectSheet(val project: Project, val tasks: List<Task>? = null, val error: String? = null)

data class PlanUiState(
    val plan: Plan? = null,
    val loading: Boolean = true,
    val refreshing: Boolean = false,
    val error: String? = null,
    val message: String? = null,
    /** Shown inside the open dialog (e.g. the meal-limit refusal), so it stays open. */
    val dialogError: String? = null,
    val saving: Boolean = false,
    val sheet: ProjectSheet? = null,
)

class PlanViewModel(private val api: SavvyApi = Graph.api) : ViewModel() {
    private val _state = MutableStateFlow(PlanUiState())
    val state: StateFlow<PlanUiState> = _state.asStateFlow()

    fun load() = fetch(false)

    fun refresh() = fetch(true)

    private fun fetch(pull: Boolean) {
        _state.update { it.copy(loading = it.plan == null, refreshing = pull && it.plan != null) }
        viewModelScope.launch {
            attempt { api.plan() }
                .onSuccess { p -> _state.update { it.copy(plan = p, loading = false, refreshing = false, error = null) } }
                .onFailure { e ->
                    _state.update {
                        if (it.plan == null) it.copy(loading = false, refreshing = false, error = friendlyError(e))
                        else it.copy(loading = false, refreshing = false, message = friendlyError(e))
                    }
                }
        }
    }

    fun clearDialogError() = _state.update { it.copy(dialogError = null) }

    fun logProgress(goal: Goal, note: String, value: String, onDone: () -> Unit) =
        save(onDone, "Progress logged.") { api.logGoalProgress(goal.id, note.trim(), value.trim()) }

    fun addMeal(main: String, kind: String, sides: List<String>, onDone: () -> Unit) =
        save(onDone, null) {
            val ok = api.addMeal(NewMeal(main.trim(), kind, sides.map { it.trim() }.filter { it.isNotEmpty() }))
            ok.message?.let { msg -> _state.update { it.copy(message = msg) } }
        }

    private fun save(onDone: () -> Unit, success: String?, call: suspend () -> Unit) {
        _state.update { it.copy(saving = true, dialogError = null) }
        viewModelScope.launch {
            attempt { call() }
                .onSuccess {
                    _state.update { it.copy(saving = false, message = success ?: it.message) }
                    onDone()
                    fetch(false)
                }
                .onFailure { e -> _state.update { it.copy(saving = false, dialogError = friendlyError(e)) } }
        }
    }

    /** Remove right away; put it back if the PC refuses. */
    fun deleteMeal(id: Long) {
        val before = _state.value.plan ?: return
        _state.update { s ->
            s.copy(plan = before.copy(meals = before.meals.copy(items = before.meals.items.filterNot { it.id == id })))
        }
        viewModelScope.launch {
            attempt { api.deleteMeal(id) }.onFailure { e ->
                _state.update { s ->
                    val cur = s.plan ?: before
                    val meal = before.meals.items.firstOrNull { it.id == id }
                    val restored = if (meal == null || cur.meals.items.any { it.id == id }) cur.meals.items
                    else (cur.meals.items + meal).sortedBy { m -> before.meals.items.indexOfFirst { it.id == m.id } }
                    s.copy(plan = cur.copy(meals = cur.meals.copy(items = restored)), message = friendlyError(e))
                }
            }
        }
    }

    fun openProject(project: Project) {
        _state.update { it.copy(sheet = ProjectSheet(project)) }
        viewModelScope.launch {
            val result = attempt { api.projectTasks(project.id) }
            _state.update { s ->
                if (s.sheet?.project?.id != project.id) s
                else s.copy(sheet = result.fold(
                    { ProjectSheet(project, tasks = it) },
                    { ProjectSheet(project, error = friendlyError(it)) },
                ))
            }
        }
    }

    fun closeProject() = _state.update { it.copy(sheet = null) }

    fun messageShown() = _state.update { it.copy(message = null) }
}

private val FRACTION = Regex("""(\d+(?:\.\d+)?)\s*(?:/|of|out of)\s*(\d+(?:\.\d+)?)""")
private val NUMBER = Regex("""\d+(?:\.\d+)?""")

/**
 * How far along a goal is, 0..1, only when the numbers really say so: an
 * explicit "2/3" (or "2 of 3") in the latest progress, or a number in the
 * progress against a number in the target ("3 pull-ups" toward "5 pull-ups").
 * Anything else is null, and the screen shows the note instead.
 */
fun goalFraction(goal: Goal): Float? {
    val progress = goal.progressNote ?: return null
    FRACTION.find(progress)?.let { m ->
        val (a, b) = m.destructured
        val total = b.toFloat()
        if (total > 0f) return (a.toFloat() / total).coerceIn(0f, 1f)
    }
    val target = goal.target?.let { NUMBER.find(it)?.value?.toFloatOrNull() } ?: return null
    if (target <= 0f) return null
    // The value is in parentheses at the end ("note (3 pull-ups)"); prefer it.
    val valuePart = Regex("""\(([^()]*)\)\s*$""").find(progress)?.groupValues?.get(1) ?: progress
    val done = NUMBER.find(valuePart)?.value?.toFloatOrNull() ?: return null
    return (done / target).coerceIn(0f, 1f)
}

package com.spirolife.savvy.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.spirolife.savvy.Graph
import com.spirolife.savvy.data.api.ApiException
import com.spirolife.savvy.data.api.Fact
import com.spirolife.savvy.data.api.Settings
import com.spirolife.savvy.data.api.Status
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import java.io.IOException

data class SettingsUiState(
    val loading: Boolean = true,
    val loadError: String? = null,
    val settings: Settings? = null,
    /** Null when the status call failed; the rest of the screen still works. */
    val status: Status? = null,
    val saving: Boolean = false,
    /** One-off note for a snackbar (a failed save, for example). */
    val message: String? = null,
)

data class MemoryUiState(
    val query: String = "",
    val loading: Boolean = true,
    val error: String? = null,
    val facts: List<Fact> = emptyList(),
)

class SettingsViewModel : ViewModel() {
    private val state = MutableStateFlow(SettingsUiState())
    val ui: StateFlow<SettingsUiState> = state.asStateFlow()

    private val memoryState = MutableStateFlow(MemoryUiState())
    val memory: StateFlow<MemoryUiState> = memoryState.asStateFlow()
    private var search: Job? = null

    init { load() }

    fun load() {
        state.update { it.copy(loading = it.settings == null, loadError = null) }
        viewModelScope.launch {
            val status = async { runCatching { Graph.api.status() }.getOrNull() }
            try {
                val settings = Graph.api.settings()
                state.update { it.copy(loading = false, settings = settings, status = status.await()) }
            } catch (e: Exception) {
                status.cancel()
                state.update { it.copy(loading = false, loadError = describe(e)) }
            }
        }
    }

    fun setTime(key: String, hour: Int, minute: Int) = update(key, JsonPrimitive(wireTime(hour, minute)))
    fun setHour(key: String, hour: Int) = update(key, JsonPrimitive(hour))
    fun setFlag(key: String, on: Boolean) = update(key, JsonPrimitive(on))
    fun setDay(day: String) = update("weekly_review_day", JsonPrimitive(day))
    fun setCheckins(times: List<String>) =
        update("checkin_times", JsonArray(times.distinct().sorted().map { JsonPrimitive(it) }))

    /** Partial update; the server answers with the full settings it saved. */
    private fun update(key: String, value: JsonElement) {
        state.update { it.copy(saving = true) }
        viewModelScope.launch {
            try {
                val saved = Graph.api.updateSettings(mapOf(key to value))
                state.update { it.copy(saving = false, settings = saved) }
            } catch (e: Exception) {
                state.update { it.copy(saving = false, message = "Couldn't save: ${describe(e)}") }
            }
        }
    }

    fun messageShown() = state.update { it.copy(message = null) }

    // ---- Memory ------------------------------------------------------------------------------

    fun searchFacts(query: String = memoryState.value.query, debounce: Boolean = false) {
        memoryState.update { it.copy(query = query, error = null) }
        search?.cancel()
        search = viewModelScope.launch {
            if (debounce) delay(300)
            memoryState.update { it.copy(loading = it.facts.isEmpty()) }
            try {
                val facts = Graph.api.facts(query.ifBlank { null })
                memoryState.update { it.copy(loading = false, facts = facts) }
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                memoryState.update { it.copy(loading = false, error = describe(e)) }
            }
        }
    }

    fun deleteFact(fact: Fact) {
        viewModelScope.launch {
            try {
                Graph.api.deleteFact(fact.id)
                memoryState.update { m -> m.copy(facts = m.facts.filterNot { it.id == fact.id }) }
            } catch (e: Exception) {
                state.update { it.copy(message = "Couldn't delete: ${describe(e)}") }
            }
        }
    }

    private fun describe(e: Exception): String = when (e) {
        is ApiException -> e.message
        is IOException -> "Can't reach your PC. Check that Tailscale is on and Savvy is running."
        else -> e.message ?: "Something went wrong."
    }
}

package com.spirolife.savvy.data.api

import kotlinx.coroutines.flow.Flow

/** Thrown for any non-2xx response; [code] is the server's machine-readable "error". */
class ApiException(val status: Int, val code: String, override val message: String) : Exception(message) {
    val unpaired: Boolean get() = status == 401
}

/**
 * Everything the app asks of the Savvy server (docs/app-api.md). Screens depend
 * on this interface only; the app-shell agent implements it in ApiClient.kt and
 * exposes it as Graph.api. All calls throw [ApiException] or IOException.
 */
interface SavvyApi {
    // Pairing (no token needed). baseUrl is what the user typed.
    suspend fun ping(baseUrl: String): Ping
    suspend fun pair(baseUrl: String, code: String, deviceName: String): PairResponse

    suspend fun status(): Status

    suspend fun googleConfig(): GoogleConfig
    suspend fun googleConnect(label: String, serverAuthCode: String): Ok
    suspend fun googleDisconnect(label: String): Ok

    suspend fun today(date: String? = null): Today
    suspend fun completeTask(id: String): Ok
    suspend fun reopenTask(id: String): Ok
    suspend fun planRestDay(day: String): Ok

    /** One chat turn as a stream of events; completes after Done or Error. */
    fun chat(message: String): Flow<ChatEvent>
    /** A turn's events from index [after] on (counted after Start), for catching up after the stream dropped. */
    suspend fun chatTurnEvents(turnId: String, after: Int): TurnEvents
    suspend fun confirm(id: String, allow: Boolean): Ok
    suspend fun chatHistory(limit: Int = 40): List<ChatMessage>

    suspend fun inbox(account: String = "all", query: String? = null, limit: Int = 30): Inbox
    suspend fun thread(account: String, threadId: String): MailThread
    suspend fun mailAction(account: String, messageId: String, action: String): Ok

    suspend fun plan(): Plan
    suspend fun logGoalProgress(id: Long, note: String, value: String = ""): Ok
    suspend fun addMeal(meal: NewMeal): Ok
    suspend fun deleteMeal(id: Long): Ok
    suspend fun projectTasks(projectId: String): List<Task>

    suspend fun diary(days: Int = 14): List<DiaryEntry>
    suspend fun addDiary(type: String, text: String): Ok
    suspend fun notes(): List<NoteSummary>
    suspend fun note(name: String): Note

    suspend fun settings(): Settings
    suspend fun updateSettings(changes: Map<String, kotlinx.serialization.json.JsonElement>): Settings
    suspend fun facts(query: String? = null): List<Fact>
    suspend fun deleteFact(id: Long): Ok

    suspend fun registerFcm(token: String): Ok
    suspend fun notifications(afterId: Long, limit: Int = 50): List<AppNotification>
}

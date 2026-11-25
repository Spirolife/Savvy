package com.spirolife.savvy.ui.chat

import com.spirolife.savvy.data.api.*
import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.json.JsonElement

/** A SavvyApi for the chat and inbox tests: override what a test needs, the rest is TODO(). */
open class ChatInboxFakeApi : SavvyApi {
    override suspend fun ping(baseUrl: String): Ping = TODO()
    override suspend fun pair(baseUrl: String, code: String, deviceName: String): PairResponse = TODO()
    override suspend fun status(): Status = TODO()
    override suspend fun googleConfig(): GoogleConfig = TODO()
    override suspend fun googleConnect(label: String, serverAuthCode: String): Ok = TODO()
    override suspend fun googleDisconnect(label: String): Ok = TODO()
    override suspend fun today(date: String?): Today = TODO()
    override suspend fun completeTask(id: String): Ok = TODO()
    override suspend fun reopenTask(id: String): Ok = TODO()
    override suspend fun planRestDay(day: String): Ok = TODO()
    override fun chat(message: String): Flow<ChatEvent> = TODO()
    override suspend fun chatTurnEvents(turnId: String, after: Int): TurnEvents = TODO()
    override suspend fun confirm(id: String, allow: Boolean): Ok = TODO()
    override suspend fun chatHistory(limit: Int, after: Double?): List<ChatMessage> = emptyList()
    override suspend fun inbox(account: String, query: String?, limit: Int): Inbox = TODO()
    override suspend fun thread(account: String, threadId: String): MailThread = TODO()
    override suspend fun mailAction(account: String, messageId: String, action: String): Ok = TODO()
    override suspend fun plan(): Plan = TODO()
    override suspend fun logGoalProgress(id: Long, note: String, value: String): Ok = TODO()
    override suspend fun addMeal(meal: NewMeal): Ok = TODO()
    override suspend fun deleteMeal(id: Long): Ok = TODO()
    override suspend fun projectTasks(projectId: String): List<Task> = TODO()
    override suspend fun diary(days: Int): List<DiaryEntry> = TODO()
    override suspend fun addDiary(type: String, text: String): Ok = TODO()
    override suspend fun notes(): List<NoteSummary> = TODO()
    override suspend fun note(name: String): Note = TODO()
    override suspend fun settings(): Settings = TODO()
    override suspend fun updateSettings(changes: Map<String, JsonElement>): Settings = TODO()
    override suspend fun facts(query: String?): List<Fact> = TODO()
    override suspend fun deleteFact(id: Long): Ok = TODO()
    override suspend fun registerFcm(token: String): Ok = TODO()
    override suspend fun notifications(afterId: Long, limit: Int): List<AppNotification> = TODO()
}

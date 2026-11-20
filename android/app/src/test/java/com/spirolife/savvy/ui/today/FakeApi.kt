package com.spirolife.savvy.ui.today

import com.spirolife.savvy.data.api.AppNotification
import com.spirolife.savvy.data.api.ChatEvent
import com.spirolife.savvy.data.api.ChatMessage
import com.spirolife.savvy.data.api.DiaryEntry
import com.spirolife.savvy.data.api.Fact
import com.spirolife.savvy.data.api.GoogleConfig
import com.spirolife.savvy.data.api.Inbox
import com.spirolife.savvy.data.api.MailThread
import com.spirolife.savvy.data.api.NewMeal
import com.spirolife.savvy.data.api.Note
import com.spirolife.savvy.data.api.NoteSummary
import com.spirolife.savvy.data.api.Ok
import com.spirolife.savvy.data.api.PairResponse
import com.spirolife.savvy.data.api.Ping
import com.spirolife.savvy.data.api.Plan
import com.spirolife.savvy.data.api.SavvyApi
import com.spirolife.savvy.data.api.Settings
import com.spirolife.savvy.data.api.Status
import com.spirolife.savvy.data.api.Task
import com.spirolife.savvy.data.api.Today
import com.spirolife.savvy.data.api.TurnEvents
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.JsonElement
import org.junit.rules.TestWatcher
import org.junit.runner.Description

/** A SavvyApi for ViewModel tests: override what a test needs, the rest fails loudly. */
open class FakeApi : SavvyApi {
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
    override suspend fun chatHistory(limit: Int): List<ChatMessage> = TODO()
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

/** Runs viewModelScope work immediately on the test thread. */
@OptIn(ExperimentalCoroutinesApi::class)
class MainDispatcherRule : TestWatcher() {
    override fun starting(description: Description) = Dispatchers.setMain(UnconfinedTestDispatcher())
    override fun finished(description: Description) = Dispatchers.resetMain()
}

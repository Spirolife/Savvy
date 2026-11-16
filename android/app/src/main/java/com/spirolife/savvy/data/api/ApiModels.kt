package com.spirolife.savvy.data.api

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/*
 * Wire models for docs/app-api.md. The JSON client uses
 * JsonNamingStrategy.SnakeCase, so camelCase here maps to snake_case on the wire.
 * Shared by every screen: add new models in your own file rather than editing
 * these, and note any contract change in your report.
 */

@Serializable data class Ping(val ok: Boolean, val name: String = "", val version: String = "")
@Serializable data class PairRequest(val code: String, val deviceName: String)
@Serializable data class PairResponse(val token: String)
@Serializable data class Ok(val ok: Boolean = true, val message: String? = null, val id: Long? = null, val email: String? = null)

// Status and Google
@Serializable data class GoogleAccount(val label: String, val email: String? = null, val connected: Boolean, val reason: String? = null)
@Serializable data class Status(
    val google: List<GoogleAccount> = emptyList(),
    val todoist: Boolean = false,
    val ollama: Boolean = false,
    val signalFallback: Boolean = false,
)
@Serializable data class GoogleConfig(val webClientId: String, val scopes: List<String>)
@Serializable data class GoogleConnectRequest(val label: String, val serverAuthCode: String)

// Today
@Serializable data class Event(
    val id: String, val account: String, val calendar: String = "", val title: String,
    val start: String, val end: String, val allDay: Boolean = false,
    val location: String? = null, val color: String? = null,
)
@Serializable data class Task(
    val id: String, val title: String, val project: String? = null, val due: String? = null,
    val priority: Int = 1, val completed: Boolean = false,
)
@Serializable data class Reminder(val id: String, val text: String, val at: String)
@Serializable data class NextUp(val event: Event, val leaveBy: String? = null, val travelMinutes: Int? = null, val travelMode: String? = null)
@Serializable data class RestSuggestion(val suggested: Boolean, val day: String? = null, val message: String = "")
@Serializable data class Today(
    val date: String,
    val next: NextUp? = null,
    val events: List<Event> = emptyList(),
    val tasks: List<Task> = emptyList(),
    val reminders: List<Reminder> = emptyList(),
    val rest: RestSuggestion? = null,
    val signinNeeded: List<String> = emptyList(),
)
@Serializable data class RestPlanRequest(val day: String)

// Chat
@Serializable data class ChatRequest(val message: String)
@Serializable data class ConfirmRequest(val id: String, val decision: String)
@Serializable data class ChatMessage(val role: String, val content: String, val ts: String? = null, val surface: String? = null)

/** One server-sent event of a chat turn (see "Chat" in docs/app-api.md). */
sealed interface ChatEvent {
    data class Text(val delta: String) : ChatEvent
    data class Tool(val name: String, val state: String, val detail: String, val warning: String?) : ChatEvent
    data class Confirm(val id: String, val name: String, val level: Int, val args: JsonObject) : ChatEvent
    data class Done(val reply: String) : ChatEvent
    data class Error(val message: String) : ChatEvent
}

// Inbox
@Serializable data class Mail(
    val id: String, val threadId: String, val account: String, val from: String, val subject: String,
    val snippet: String = "", val date: String = "", val unread: Boolean = false,
    val starred: Boolean = false, val important: Boolean = false,
)
@Serializable data class Inbox(val summary: String? = null, val unread: Map<String, Int> = emptyMap(), val messages: List<Mail> = emptyList())
@Serializable data class ThreadMessage(val id: String, val from: String, val to: String = "", val date: String = "", val bodyText: String = "")
@Serializable data class MailThread(val subject: String, val messages: List<ThreadMessage>)
@Serializable data class MailAction(val action: String)

// Plan
@Serializable data class Goal(
    val id: Long, val title: String, val category: String? = null, val target: String? = null,
    val status: String = "active", val progressNote: String? = null, val lastLogged: String? = null,
)
@Serializable data class Meal(val id: Long, val main: String, val kind: String = "meal", val sides: List<String> = emptyList(), val day: String? = null)
@Serializable data class MealWeek(val week: String? = null, val diet: String? = null, val items: List<Meal> = emptyList())
@Serializable data class Project(val id: String, val name: String, val openCount: Int = 0)
@Serializable data class Plan(val goals: List<Goal> = emptyList(), val meals: MealWeek = MealWeek(), val projects: List<Project> = emptyList())
@Serializable data class GoalProgress(val note: String, val value: String = "")
@Serializable data class NewMeal(val main: String, val kind: String = "meal", val sides: List<String> = emptyList())

// Diary and notes
@Serializable data class DiaryEntry(val id: String, val type: String, val text: String, val ts: String)
@Serializable data class NewDiaryEntry(val type: String, val text: String)
@Serializable data class NoteSummary(val name: String, val updated: String? = null)
@Serializable data class Note(val name: String, val text: String)

// Settings and memory
@Serializable data class Settings(
    val bodTime: String = "08:30",
    val eodTime: String = "22:00",
    val checkinTimes: List<String> = emptyList(),
    val weeklyReviewDay: String = "Sunday",
    val weeklyReviewTime: String = "18:00",
    val quietHoursStart: Int = 22,
    val quietHoursEnd: Int = 8,
    val restDayWarnings: Boolean = true,
    val signalFallback: Boolean = true,
)
@Serializable data class Fact(val id: Long, val fact: String, val type: String = "", val updated: String? = null)

// Notifications
@Serializable data class FcmRegistration(val fcmToken: String)
@Serializable data class AppNotification(val id: Long, val ts: String, val kind: String, val title: String, val body: String)

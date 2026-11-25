package com.spirolife.savvy.data

import com.spirolife.savvy.data.api.ApiException
import com.spirolife.savvy.data.api.AppNotification
import com.spirolife.savvy.data.api.ChatEvent
import com.spirolife.savvy.data.api.ChatMessage
import com.spirolife.savvy.data.api.ChatRequest
import com.spirolife.savvy.data.api.ConfirmRequest
import com.spirolife.savvy.data.api.DiaryEntry
import com.spirolife.savvy.data.api.Fact
import com.spirolife.savvy.data.api.FcmRegistration
import com.spirolife.savvy.data.api.GoalProgress
import com.spirolife.savvy.data.api.GoogleConfig
import com.spirolife.savvy.data.api.GoogleConnectRequest
import com.spirolife.savvy.data.api.Inbox
import com.spirolife.savvy.data.api.MailAction
import com.spirolife.savvy.data.api.MailThread
import com.spirolife.savvy.data.api.NewDiaryEntry
import com.spirolife.savvy.data.api.NewMeal
import com.spirolife.savvy.data.api.Note
import com.spirolife.savvy.data.api.NoteSummary
import com.spirolife.savvy.data.api.Ok
import com.spirolife.savvy.data.api.PairRequest
import com.spirolife.savvy.data.api.PairResponse
import com.spirolife.savvy.data.api.Ping
import com.spirolife.savvy.data.api.Plan
import com.spirolife.savvy.data.api.RestPlanRequest
import com.spirolife.savvy.data.api.SavvyApi
import com.spirolife.savvy.data.api.Settings
import com.spirolife.savvy.data.api.Status
import com.spirolife.savvy.data.api.Task
import com.spirolife.savvy.data.api.Today
import com.spirolife.savvy.data.api.TurnEvents
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.buffer
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNamingStrategy
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** The wire format from docs/app-api.md: snake_case, lenient about extra or missing fields. */
@OptIn(ExperimentalSerializationApi::class)
val SavvyJson = Json {
    namingStrategy = JsonNamingStrategy.SnakeCase
    ignoreUnknownKeys = true
    explicitNulls = false
    encodeDefaults = true
}

/** The port the Savvy server listens on unless config.json says otherwise. */
const val DEFAULT_PORT = 8765

/**
 * Turns what the user typed ("my-pc", "100.64.0.3:8765", "http://my-pc.ts.net:8765/")
 * into a base URL: adds http:// when there is no scheme, the default port when
 * a bare host was typed, and strips trailing slashes.
 * @throws IllegalArgumentException when it can't be a URL.
 */
fun normalizeBaseUrl(input: String): String {
    var s = input.trim()
    require(s.isNotEmpty() && s.none { it.isWhitespace() }) { "Enter your PC's address." }
    val hadScheme = s.contains("://")
    if (!hadScheme) s = "http://$s"
    val url = s.toHttpUrlOrNull() ?: throw IllegalArgumentException("That doesn't look like an address.")
    val explicitPort = Regex("^[a-zA-Z]+://(\\[[^]]+]|[^/:]+):\\d+").containsMatchIn(s)
    val port = if (!hadScheme && !explicitPort) DEFAULT_PORT else url.port
    val withPort = url.newBuilder().port(port).build()
    return withPort.toString().trimEnd('/')
}

/**
 * [SavvyApi] over OkHttp. Reads the base URL and device token on every call,
 * so pairing and unpairing take effect immediately.
 *
 * @param onUnpaired called when the server answers 401: the token is no longer
 *   valid and the app should return to pairing.
 */
class ApiClient(
    private val baseUrl: () -> String?,
    private val token: () -> String?,
    private val onUnpaired: () -> Unit,
    private val http: OkHttpClient = defaultHttpClient(),
) : SavvyApi {
    // Chat turns can pause for minutes on a confirm sheet; give the stream room.
    private val streamHttp = http.newBuilder().readTimeout(10, TimeUnit.MINUTES).callTimeout(0, TimeUnit.SECONDS).build()
    private val json = SavvyJson

    // ---- Pairing -------------------------------------------------------------------------

    override suspend fun ping(baseUrl: String): Ping =
        decode(send("GET", url(normalizeBaseUrl(baseUrl), "v1", "ping"), auth = false))

    override suspend fun pair(baseUrl: String, code: String, deviceName: String): PairResponse =
        decode(send("POST", url(normalizeBaseUrl(baseUrl), "v1", "pair"), encode(PairRequest(code, deviceName)), auth = false))

    // ---- Status and Google ---------------------------------------------------------------

    override suspend fun status(): Status = get("v1", "status")
    override suspend fun googleConfig(): GoogleConfig = get("v1", "google", "config")
    override suspend fun googleConnect(label: String, serverAuthCode: String): Ok =
        post(encode(GoogleConnectRequest(label, serverAuthCode)), "v1", "google", "connect")
    override suspend fun googleDisconnect(label: String): Ok = delete("v1", "google", label)

    // ---- Today ---------------------------------------------------------------------------

    override suspend fun today(date: String?): Today = get("v1", "today", query = mapOf("date" to date))
    override suspend fun completeTask(id: String): Ok = post(null, "v1", "tasks", id, "complete")
    override suspend fun reopenTask(id: String): Ok = post(null, "v1", "tasks", id, "reopen")
    override suspend fun planRestDay(day: String): Ok = post(encode(RestPlanRequest(day)), "v1", "rest", "plan")

    // ---- Chat ----------------------------------------------------------------------------

    override fun chat(message: String): Flow<ChatEvent> = callbackFlow {
        val request = authedRequest(apiUrl("v1", "chat"))
            .header("Accept", "text/event-stream")
            .post(encode(ChatRequest(message)).toRequestBody(JSON_TYPE))
            .build()
        val listener = object : EventSourceListener() {
            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                val event = parseChatEvent(type, data) ?: return
                trySend(event)
                if (event is ChatEvent.Done || event is ChatEvent.Error) close()
            }

            override fun onClosed(eventSource: EventSource) {
                close(IOException("The connection to your PC closed before Savvy finished."))
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                val error = if (response != null && !response.isSuccessful) {
                    apiError(response.code, runCatching { response.body?.string() }.getOrNull().orEmpty(), authed = true)
                } else {
                    t as? IOException ?: IOException(t?.message ?: "The chat stream broke.", t)
                }
                close(error)
            }
        }
        val source = EventSources.createFactory(streamHttp).newEventSource(request, listener)
        awaitClose { source.cancel() }
    }.buffer(Channel.UNLIMITED).flowOn(Dispatchers.IO)

    override suspend fun chatTurnEvents(turnId: String, after: Int): TurnEvents =
        parseTurnEvents(send("GET", apiUrl("v1", "chat", "turns", turnId, "events", query = mapOf("after" to after.toString()))))

    override suspend fun confirm(id: String, allow: Boolean): Ok =
        post(encode(ConfirmRequest(id, if (allow) "allow" else "deny")), "v1", "chat", "confirm")

    override suspend fun chatHistory(limit: Int, after: Double?): List<ChatMessage> =
        get("v1", "chat", "history", query = mapOf("limit" to limit.toString(), "after" to after?.toString()))

    // ---- Inbox ---------------------------------------------------------------------------

    override suspend fun inbox(account: String, query: String?, limit: Int): Inbox =
        get("v1", "inbox", query = mapOf("account" to account, "q" to query?.takeIf { it.isNotBlank() }, "limit" to limit.toString()))

    override suspend fun thread(account: String, threadId: String): MailThread =
        get("v1", "inbox", account, "threads", threadId)

    override suspend fun mailAction(account: String, messageId: String, action: String): Ok =
        post(encode(MailAction(action)), "v1", "inbox", account, "messages", messageId, "action")

    // ---- Plan ----------------------------------------------------------------------------

    override suspend fun plan(): Plan = get("v1", "plan")
    override suspend fun logGoalProgress(id: Long, note: String, value: String): Ok =
        post(encode(GoalProgress(note, value)), "v1", "goals", id.toString(), "progress")
    override suspend fun addMeal(meal: NewMeal): Ok = post(encode(meal), "v1", "meals")
    override suspend fun deleteMeal(id: Long): Ok = delete("v1", "meals", id.toString())
    override suspend fun projectTasks(projectId: String): List<Task> = get("v1", "projects", projectId, "tasks")

    // ---- Diary and notes -----------------------------------------------------------------

    override suspend fun diary(days: Int): List<DiaryEntry> = get("v1", "diary", query = mapOf("days" to days.toString()))
    override suspend fun addDiary(type: String, text: String): Ok = post(encode(NewDiaryEntry(type, text)), "v1", "diary")
    override suspend fun notes(): List<NoteSummary> = get("v1", "notes")
    override suspend fun note(name: String): Note = get("v1", "notes", name)

    // ---- Settings and memory -------------------------------------------------------------

    override suspend fun settings(): Settings = get("v1", "settings")
    override suspend fun updateSettings(changes: Map<String, JsonElement>): Settings =
        decode(send("PUT", apiUrl("v1", "settings"), encode(JsonObject(changes))))
    override suspend fun facts(query: String?): List<Fact> =
        get("v1", "memory", "facts", query = mapOf("q" to query?.takeIf { it.isNotBlank() }))
    override suspend fun deleteFact(id: Long): Ok = delete("v1", "memory", "facts", id.toString())

    // ---- Notifications -------------------------------------------------------------------

    override suspend fun registerFcm(token: String): Ok = post(encode(FcmRegistration(token)), "v1", "devices", "fcm")
    override suspend fun notifications(afterId: Long, limit: Int): List<AppNotification> =
        get("v1", "notifications", query = mapOf("after_id" to afterId.toString(), "limit" to limit.toString()))

    // ---- Plumbing ------------------------------------------------------------------------

    private suspend inline fun <reified T> get(vararg segments: String, query: Map<String, String?> = emptyMap()): T =
        decode(send("GET", apiUrl(*segments, query = query)))

    private suspend inline fun <reified T> post(body: String?, vararg segments: String): T =
        decode(send("POST", apiUrl(*segments), body ?: "{}"))

    private suspend inline fun <reified T> delete(vararg segments: String): T =
        decode(send("DELETE", apiUrl(*segments)))

    private inline fun <reified T> encode(value: T): String = json.encodeToString(value)

    private inline fun <reified T> decode(text: String): T = json.decodeFromString(text)

    /** URL on the paired PC; a missing address means this phone isn't paired. */
    private fun apiUrl(vararg segments: String, query: Map<String, String?> = emptyMap()): HttpUrl {
        val base = baseUrl() ?: throw unpairedError()
        return url(base, *segments, query = query)
    }

    private fun url(base: String, vararg segments: String, query: Map<String, String?> = emptyMap()): HttpUrl {
        val builder = (base.toHttpUrlOrNull() ?: throw IllegalArgumentException("Bad server address: $base")).newBuilder()
        segments.forEach { builder.addPathSegment(it) }
        query.forEach { (k, v) -> if (v != null) builder.addQueryParameter(k, v) }
        return builder.build()
    }

    private fun authedRequest(url: HttpUrl): Request.Builder {
        val t = token() ?: throw unpairedError()
        return Request.Builder().url(url).header("Authorization", "Bearer $t")
    }

    private fun unpairedError(): ApiException {
        onUnpaired()
        return ApiException(401, "unpaired", "This phone isn't paired with your PC.")
    }

    /** Sends a request and returns the body text of a 2xx answer; throws [ApiException] otherwise. */
    private suspend fun send(method: String, url: HttpUrl, body: String? = null, auth: Boolean = true): String =
        withContext(Dispatchers.IO) {
            val builder = if (auth) authedRequest(url) else Request.Builder().url(url)
            val request = builder.method(method, body?.toRequestBody(JSON_TYPE)).build()
            http.newCall(request).await().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) throw apiError(response.code, text, auth)
                text
            }
        }

    /** Builds the [ApiException] for a non-2xx answer, and signals unpairing on 401. */
    private fun apiError(status: Int, body: String, authed: Boolean): ApiException {
        if (status == 401 && authed) onUnpaired()
        val obj = runCatching { json.parseToJsonElement(body).jsonObject }.getOrNull()
        val code = obj?.string("error") ?: if (status == 401) "unpaired" else "http_$status"
        val message = obj?.string("message")
            ?: obj?.string("detail")
            ?: if (status == 401) "This phone isn't paired with your PC any more." else "Your PC answered with an error ($status)."
        return ApiException(status, code, message)
    }

    companion object {
        private val JSON_TYPE = "application/json; charset=utf-8".toMediaType()

        fun defaultHttpClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS) // some answers (inbox summary, rest planning) call the model
            .writeTimeout(30, TimeUnit.SECONDS)
            .build()
    }
}

/** Maps one SSE event of a chat turn to a [ChatEvent]; unknown or malformed events give null. */
internal fun parseChatEvent(type: String?, data: String): ChatEvent? {
    val o = runCatching { SavvyJson.parseToJsonElement(data).jsonObject }.getOrNull() ?: return null
    return when (type) {
        "start" -> ChatEvent.Start(o.string("id") ?: return null)
        "text" -> ChatEvent.Text(o.string("delta").orEmpty())
        "tool" -> ChatEvent.Tool(
            name = o.string("name").orEmpty(),
            state = o.string("state").orEmpty(),
            detail = o.string("detail").orEmpty(),
            warning = o.string("warning"),
        )
        "confirm" -> ChatEvent.Confirm(
            id = o.string("id") ?: return null,
            name = o.string("name").orEmpty(),
            level = (o["level"] as? JsonPrimitive)?.intOrNull ?: 0,
            args = o["args"] as? JsonObject ?: JsonObject(emptyMap()),
        )
        "done" -> ChatEvent.Done(o.string("reply").orEmpty())
        "error" -> ChatEvent.Error(o.string("message") ?: "Something went wrong on your PC.")
        else -> null
    }
}

/** `{"events": [{"event": "text", "data": {...}}], "next": 7}`; events the app doesn't know are skipped. */
internal fun parseTurnEvents(body: String): TurnEvents {
    val o = SavvyJson.parseToJsonElement(body).jsonObject
    val events = (o["events"] as? JsonArray).orEmpty().mapNotNull { e ->
        val item = e as? JsonObject ?: return@mapNotNull null
        parseChatEvent(item.string("event"), (item["data"] as? JsonObject ?: return@mapNotNull null).toString())
    }
    return TurnEvents(events, (o["next"] as? JsonPrimitive)?.intOrNull ?: 0)
}

private fun JsonObject.string(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull

/** Runs the call on OkHttp's dispatcher; cancelling the coroutine cancels the call. */
private suspend fun Call.await(): Response = suspendCancellableCoroutine { cont ->
    enqueue(object : Callback {
        override fun onResponse(call: Call, response: Response) = cont.resume(response) { _ -> response.close() }
        override fun onFailure(call: Call, e: IOException) = cont.resumeWithException(e)
    })
    cont.invokeOnCancellation { runCatching { cancel() } }
}

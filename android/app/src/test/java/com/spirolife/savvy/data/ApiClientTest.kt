package com.spirolife.savvy.data

import com.spirolife.savvy.data.api.ApiException
import com.spirolife.savvy.data.api.ChatEvent
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class ApiClientTest {
    private lateinit var server: MockWebServer
    private var token: String? = "tok123"
    private var unpairedCalls = 0
    private lateinit var api: ApiClient

    @Before fun setUp() {
        server = MockWebServer().apply { start() }
        val base = server.url("/").toString()
        api = ApiClient(baseUrl = { base }, token = { token }, onUnpaired = { unpairedCalls++; token = null })
    }

    @After fun tearDown() = server.shutdown()

    private fun json(body: String, code: Int = 200) =
        MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body)

    @Test fun sendsBearerTokenAndDecodesSnakeCase() = runBlocking {
        server.enqueue(json("""{"google":[{"label":"school","email":null,"connected":false,"reason":"no saved login","extra":1}],
            "todoist":true,"ollama":false,"signal_fallback":true}"""))
        val status = api.status()
        val req = server.takeRequest()
        assertEquals("GET", req.method)
        assertEquals("/v1/status", req.path)
        assertEquals("Bearer tok123", req.getHeader("Authorization"))
        assertTrue(status.signalFallback)
        assertTrue(status.todoist)
        assertEquals("no saved login", status.google.single().reason)
        assertNull(status.google.single().email)
    }

    @Test fun encodesSnakeCaseBodiesAndQueries() = runBlocking {
        server.enqueue(json("""{"ok":true,"email":"a@b.c","message":"signed in"}"""))
        val ok = api.googleConnect("school", "4/0Acode")
        val req = server.takeRequest()
        assertEquals("/v1/google/connect", req.path)
        assertEquals("""{"label":"school","server_auth_code":"4/0Acode"}""", req.body.readUtf8())
        assertEquals("a@b.c", ok.email)

        server.enqueue(json("[]"))
        api.notifications(afterId = 41)
        assertEquals("/v1/notifications?after_id=41&limit=50", server.takeRequest().path)

        server.enqueue(json("""{"messages":[]}"""))
        api.inbox("school", query = "exam week")
        assertEquals("/v1/inbox?account=school&q=exam%20week&limit=30", server.takeRequest().path)

        server.enqueue(json("""{"bod_time":"07:45"}"""))
        val saved = api.updateSettings(mapOf("bod_time" to JsonPrimitive("07:45")))
        val put = server.takeRequest()
        assertEquals("PUT", put.method)
        assertEquals("""{"bod_time":"07:45"}""", put.body.readUtf8())
        assertEquals("07:45", saved.bodTime)
    }

    @Test fun pairNeedsNoTokenAndNormalizesTheAddress() = runBlocking {
        token = null
        server.enqueue(json("""{"token":"new"}"""))
        val typed = "${server.hostName}:${server.port}/"
        val res = api.pair(typed, "123456", "Pixel 8")
        val req = server.takeRequest()
        assertNull(req.getHeader("Authorization"))
        assertEquals("""{"code":"123456","device_name":"Pixel 8"}""", req.body.readUtf8())
        assertEquals("new", res.token)
        assertEquals(0, unpairedCalls)
    }

    @Test fun errorsCarryCodeAndMessage() = runBlocking {
        server.enqueue(json("""{"error":"wrong_account","message":"That's not the school account."}""", 409))
        try {
            api.googleConnect("school", "x")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(409, e.status)
            assertEquals("wrong_account", e.code)
            assertEquals("That's not the school account.", e.message)
            assertFalse(e.unpaired)
        }
        assertEquals(0, unpairedCalls)
    }

    @Test fun errorWithoutJsonBodyStillReadable() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(502).setBody("Bad gateway"))
        try {
            api.plan()
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals("http_502", e.code)
            assertTrue(e.message.contains("502"))
        }
    }

    @Test fun unauthorizedClearsTokenAndSignalsUnpaired() = runBlocking {
        server.enqueue(json("""{"error":"unpaired"}""", 401))
        try {
            api.today()
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertTrue(e.unpaired)
            assertEquals("unpaired", e.code)
        }
        assertEquals(1, unpairedCalls)
        assertNull(token)
    }

    @Test fun badPairingCodeDoesNotUnpair() = runBlocking {
        server.enqueue(json("""{"error":"bad_code","message":"Wrong or expired code."}""", 403))
        try {
            api.pair(server.url("/").toString(), "000000", "Pixel")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals("bad_code", e.code)
        }
        assertEquals(0, unpairedCalls)
    }

    @Test fun chatParsesServerSentEvents() = runBlocking {
        val stream = """
            |event: text
            |data: {"delta":"Moving "}
            |
            |event: tool
            |data: {"name":"move_event","state":"done","detail":"Moved it","warning":null}
            |
            |event: confirm
            |data: {"id":"c1","name":"send_email","level":2,"args":{"to":"a@b.c","subject":"Hi"}}
            |
            |event: ping
            |data: {}
            |
            |event: text
            |data: {"delta":"done."}
            |
            |event: done
            |data: {"reply":"Moving done."}
            |
            |""".trimMargin()
        server.enqueue(MockResponse().setHeader("Content-Type", "text/event-stream").setBody(stream))

        val events = api.chat("move my 10am").toList()

        val req = server.takeRequest()
        assertEquals("/v1/chat", req.path)
        assertEquals("Bearer tok123", req.getHeader("Authorization"))
        assertEquals("""{"message":"move my 10am"}""", req.body.readUtf8())

        assertEquals(5, events.size)
        assertEquals(ChatEvent.Text("Moving "), events[0])
        assertEquals(ChatEvent.Tool("move_event", "done", "Moved it", null), events[1])
        val confirm = events[2] as ChatEvent.Confirm
        assertEquals("c1", confirm.id)
        assertEquals("send_email", confirm.name)
        assertEquals(2, confirm.level)
        assertEquals("a@b.c", confirm.args["to"]!!.jsonPrimitive.content)
        assertEquals(ChatEvent.Done("Moving done."), events[4])
    }

    @Test fun chatErrorEventEndsTheStream() = runBlocking {
        server.enqueue(MockResponse().setHeader("Content-Type", "text/event-stream")
            .setBody("event: error\ndata: {\"message\":\"Claude is down\"}\n\n"))
        assertEquals(listOf<ChatEvent>(ChatEvent.Error("Claude is down")), api.chat("hi").toList())
    }

    @Test fun chatBusyBecomesApiException() = runBlocking {
        server.enqueue(json("""{"error":"busy","message":"Savvy is still answering."}""", 409))
        try {
            api.chat("hi").toList()
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals("busy", e.code)
            assertEquals("Savvy is still answering.", e.message)
        }
    }

    @Test fun parseChatEventIgnoresJunk() {
        assertNull(parseChatEvent("text", "not json"))
        assertNull(parseChatEvent("mystery", "{}"))
        assertEquals(ChatEvent.Text(""), parseChatEvent("text", "{}"))
    }

    @Test fun normalizesBaseUrls() {
        assertEquals("http://my-pc:8765", normalizeBaseUrl("my-pc"))
        assertEquals("http://100.64.0.3:8765", normalizeBaseUrl(" 100.64.0.3 "))
        assertEquals("http://100.64.0.3:9000", normalizeBaseUrl("100.64.0.3:9000/"))
        assertEquals("http://my-pc.tail1234.ts.net:8765", normalizeBaseUrl("http://my-pc.tail1234.ts.net:8765//"))
        assertEquals("https://savvy.example.com", normalizeBaseUrl("https://savvy.example.com/"))
        assertEquals("http://my-pc:8765", normalizeBaseUrl("HTTP://My-PC:8765"))
    }

    @Test fun rejectsAddressesThatCannotBeUrls() {
        for (bad in listOf("", "   ", "my pc", "http://")) {
            try {
                normalizeBaseUrl(bad)
                fail("accepted '$bad'")
            } catch (_: IllegalArgumentException) {
            }
        }
    }
}

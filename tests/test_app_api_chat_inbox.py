"""App API: chat (sse, confirmations, history) and inbox. Run: python -m unittest discover tests

core.respond, the Anthropic client, Memory and every Gmail call are fakes: nothing
here touches the network, credentials/ or memory/.
"""

import json
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import core
import email_integration
import memory
import tool_diagnostics
from app_api import routes_chat, routes_inbox
from app_api.auth import require_device
from app_api.server import ApiError


def make_app() -> FastAPI:
    """Only this slice's routers, so other areas' work in progress can't break these tests."""
    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status)

    app.include_router(routes_chat.router)
    app.include_router(routes_inbox.router)
    app.dependency_overrides[require_device] = lambda: None     # pairing is tested elsewhere
    return app


def parse_sse(lines) -> list[tuple[str, dict]]:
    events, name = [], None
    for line in lines:
        if line.startswith("event: "):
            name = line[len("event: "):]
        elif line.startswith("data: "):
            events.append((name, json.loads(line[len("data: "):])))
    return events


class FakeMemory:
    rows: list[dict] = []
    instances: list["FakeMemory"] = []

    def __init__(self, session_id=None):
        self.session_id = session_id
        self.db = mock.MagicMock()
        FakeMemory.instances.append(self)

    def retrieve_recent(self, n=10):
        return FakeMemory.rows[-n:]


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(make_app())
        FakeMemory.instances = []
        self.decisions = []
        patches = [
            mock.patch.object(routes_chat, "get_client", return_value=object()),
            mock.patch.object(memory, "Memory", FakeMemory),
            mock.patch.object(core, "finish_pasted_signin", return_value=None),
            mock.patch.object(core, "respond", side_effect=self.fake_respond),
            mock.patch.object(tool_diagnostics, "_audit_write"),     # memory/ stays untouched
            mock.patch.object(tool_diagnostics, "get_danger_level",
                              side_effect=lambda n: 0 if n == "list_events" else 2),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: self.assertFalse(routes_chat._turn_lock.locked()))

    def fake_respond(self, client, mem, text, surface="repl", stream_callback=None,
                     tool_event_callback=None, **_):
        self.assertEqual(surface, "app")
        self.assertEqual(mem.session_id, "app")
        stream_callback("Hel")
        stream_callback("lo")
        tool_event_callback("list_events", {}, json.dumps([1, 2]))
        if "email" in text:
            decision = tool_diagnostics._prompt_handler(
                "send_email", {"to": "a@example.com", "subject": "Hi", "_importance": "high"}, "x1", 2)
            self.decisions.append(decision)
            result = (json.dumps({"success": True, "message": "sent"}) if decision == "approve"
                      else tool_diagnostics.log_denied("x1", "send_email"))
            tool_event_callback("send_email", {}, result)
        return "Hello", 0, 0

    def stream_turn(self, message, answer=None):
        """Run one turn; `answer` ("allow"/"deny") answers a confirm through /confirm."""
        events, name = [], None
        with self.client.stream("POST", "/v1/chat", json={"message": message}) as r:
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.headers["content-type"].startswith("text/event-stream"))
            for line in r.iter_lines():
                if line.startswith("event: "):
                    name = line[len("event: "):]
                elif line.startswith("data: "):
                    data = json.loads(line[len("data: "):])
                    if name == "start":
                        self.turn_id = data["id"]
                        continue
                    events.append((name, data))
                    if name == "confirm" and answer:
                        res = self.client.post("/v1/chat/confirm", json={"id": data["id"], "decision": answer})
                        self.assertEqual(res.json(), {"ok": True})
        return events

    def test_stream_framing_and_order(self):
        with self.client.stream("POST", "/v1/chat", json={"message": "hi"}) as r:
            raw = "".join(r.iter_text())
        self.assertRegex(raw, r'^event: start\ndata: \{"id": "[0-9a-f]{12}"\}\n\nevent: text\ndata: \{"delta": "Hel"\}\n\n')
        events = parse_sse(raw.splitlines())
        self.assertEqual([e for e, _ in events], ["start", "text", "text", "tool", "done"])
        self.assertEqual(events[3][1], {"name": "list_events", "state": "read",
                                        "detail": "2 item(s)", "warning": None})
        self.assertEqual(events[-1][1], {"reply": "Hello"})

    def test_confirm_allow(self):
        events = self.stream_turn("send an email", answer="allow")
        names = [e for e, _ in events]
        self.assertEqual(names, ["text", "text", "tool", "confirm", "tool", "done"])
        confirm = events[3][1]
        self.assertEqual(confirm["name"], "send_email")
        self.assertEqual(confirm["level"], 2)
        self.assertEqual(confirm["args"], {"to": "a@example.com", "subject": "Hi"})
        self.assertEqual(self.decisions, ["approve"])
        self.assertEqual(events[4][1]["state"], "done")
        self.assertEqual(routes_chat._pending, {})

    def test_confirm_deny(self):
        events = self.stream_turn("send an email", answer="deny")
        self.assertEqual(self.decisions, ["deny"])
        self.assertEqual(events[4][1]["state"], "denied")

    def test_confirm_timeout_denies(self):
        with mock.patch.object(routes_chat, "CONFIRM_TIMEOUT", 0.2):
            events = self.stream_turn("send an email")
        self.assertEqual(self.decisions, ["deny"])
        self.assertEqual(events[-1][0], "done")

    def test_confirm_unknown_id_404(self):
        res = self.client.post("/v1/chat/confirm", json={"id": "c999", "decision": "allow"})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"], "not_waiting")

    def test_busy_409(self):
        routes_chat._turn_lock.acquire()
        try:
            res = self.client.post("/v1/chat", json={"message": "hi"})
        finally:
            routes_chat._turn_lock.release()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"], "busy")

    def wait_for_turn_end(self):
        deadline = time.monotonic() + 5
        while routes_chat._turn_lock.locked() and time.monotonic() < deadline:
            time.sleep(0.02)

    def test_catch_up_after_the_stream_finished(self):
        self.stream_turn("hi")
        res = self.client.get(f"/v1/chat/turns/{self.turn_id}/events?after=0")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        # The two text deltas come back as one.
        self.assertEqual([(e["event"], e["data"]) for e in body["events"]][:1], [("text", {"delta": "Hello"})])
        self.assertEqual([e["event"] for e in body["events"]], ["text", "tool", "done"])
        self.assertEqual(body["next"], 4)
        later = self.client.get(f"/v1/chat/turns/{self.turn_id}/events?after=3").json()
        self.assertEqual([e["event"] for e in later["events"]], ["done"])
        self.assertEqual(self.client.get(f"/v1/chat/turns/{self.turn_id}/events?after=4").json(),
                         {"events": [], "next": 4})

    def test_unknown_turn_404(self):
        res = self.client.get("/v1/chat/turns/nope/events")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"], "no_turn")

    def test_disconnect_keeps_confirm_open_for_catch_up(self):
        # The test client can't drop a connection mid-stream, so do what a real
        # disconnect does on the server: abandon the turn.
        with mock.patch.object(routes_chat, "KEEPALIVE", 0.05):
            streamer = threading.Thread(target=self.stream_turn, args=("send an email",), daemon=True)
            streamer.start()
            deadline = time.monotonic() + 5
            while not routes_chat._pending and time.monotonic() < deadline:
                time.sleep(0.02)
            turn = routes_chat._last_turn
            turn.abandon()
            time.sleep(0.1)
            # The stream is gone, but the turn still waits for an answer.
            self.assertTrue(routes_chat._turn_lock.locked())
            self.assertEqual(self.decisions, [])
            events = self.client.get(f"/v1/chat/turns/{turn.id}/events").json()["events"]
            confirm = [e for e in events if e["event"] == "confirm"]
            self.assertEqual(len(confirm), 1)
            res = self.client.post("/v1/chat/confirm", json={"id": confirm[0]["data"]["id"], "decision": "allow"})
            self.assertEqual(res.json(), {"ok": True})
            self.wait_for_turn_end()
            streamer.join(5)
            self.assertFalse(streamer.is_alive())
        self.assertEqual(self.decisions, ["approve"])
        events = self.client.get(f"/v1/chat/turns/{turn.id}/events").json()["events"]
        self.assertEqual([e["event"] for e in events], ["text", "tool", "tool", "done"])   # answered confirm left out

    def test_disconnected_confirm_still_times_out(self):
        with mock.patch.object(routes_chat, "CONFIRM_TIMEOUT", 0.2):
            with self.client.stream("POST", "/v1/chat", json={"message": "send an email"}) as r:
                for line in r.iter_lines():
                    if line == "event: confirm":
                        break
            self.wait_for_turn_end()
        self.assertEqual(self.decisions, ["deny"])

    def test_error_event(self):
        core.respond.side_effect = RuntimeError("model down")
        events = self.stream_turn("hi")
        self.assertEqual(events, [("error", {"message": "model down"})])

    def test_pasted_signin_short_circuits(self):
        core.finish_pasted_signin.return_value = "'school' is signed in."
        events = self.stream_turn("http://localhost/?code=abc&state=x")
        self.assertEqual(events, [("done", {"reply": "'school' is signed in."})])
        core.respond.assert_not_called()
        self.assertEqual(FakeMemory.instances, [])

    def test_history_maps_surfaces(self):
        FakeMemory.rows = [
            {"role": "user", "content": "[Signal] from the phone", "timestamp": 1_700_000_000.0},
            {"role": "assistant", "content": "[App] from the app", "timestamp": 1_700_000_001.0},
            {"role": "user", "content": "at the desk", "timestamp": 1_700_000_002.0},
        ]
        res = self.client.get("/v1/chat/history?limit=2")
        self.assertEqual(res.status_code, 200)
        items = res.json()
        self.assertEqual([(i["role"], i["content"], i["surface"]) for i in items],
                         [("assistant", "from the app", "app"), ("user", "at the desk", "desktop")])
        self.assertRegex(items[0]["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")

    def test_app_surface_is_tagged(self):
        self.assertEqual(core.SURFACE_TAG["app"], "[App] ")


def fake_gmail(messages: list[dict]):
    """A Gmail service whose list() returns `messages` (each {"id", "thread", "ms", "labels", ...})."""
    svc = mock.MagicMock()
    api = svc.users.return_value.messages.return_value
    api.list.return_value.execute.return_value = {"messages": [{"id": m["id"]} for m in messages]}
    by_id = {m["id"]: m for m in messages}

    def get(userId, id, **_):
        m = by_id[id]
        return SimpleNamespace(execute=lambda: {
            "threadId": m["thread"], "internalDate": str(m["ms"]), "labelIds": m["labels"],
            "snippet": "snip " + id,
            "payload": {"headers": [{"name": "From", "value": "x@example.com"},
                                    {"name": "Subject", "value": "S " + id}]}})
    api.get.side_effect = get
    return svc


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(make_app())
        routes_inbox._summary.update(text=None, at=None)
        self.personal = fake_gmail([
            {"id": "p1", "thread": "t1", "ms": 3000, "labels": ["INBOX", "UNREAD", "IMPORTANT"]},
            {"id": "p2", "thread": "t2", "ms": 1000, "labels": ["INBOX", "STARRED"]},
        ])
        self.school = fake_gmail([{"id": "s1", "thread": "t9", "ms": 2000, "labels": ["INBOX"]}])
        patches = [
            mock.patch.object(email_integration, "_get_services",
                              return_value=[("personal", self.personal), ("school", self.school)]),
            mock.patch.object(email_integration, "get_unread_count", return_value={"personal": 1, "school": 0}),
            mock.patch.object(email_integration, "get_important_unread", return_value=[]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_inbox_all_accounts_newest_first(self):
        res = self.client.get("/v1/inbox")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["unread"], {"personal": 1, "school": 0})
        self.assertIsNone(body["summary"])
        self.assertEqual([m["id"] for m in body["messages"]], ["p1", "s1", "p2"])
        p1 = body["messages"][0]
        self.assertEqual((p1["thread_id"], p1["account"], p1["unread"], p1["starred"], p1["important"]),
                         ("t1", "personal", True, False, True))
        self.assertEqual(body["messages"][2]["starred"], True)
        self.assertIn("T", p1["date"])
        _, kwargs = self.personal.users.return_value.messages.return_value.list.call_args
        self.assertEqual(kwargs["q"], routes_inbox.IMPORTANT_QUERY)
        email_integration.get_unread_count.assert_called_with(routes_inbox.IMPORTANT_QUERY)

    def test_inbox_account_filter_query_and_limit(self):
        res = self.client.get("/v1/inbox?account=personal&q=from:boss&limit=1")
        self.assertEqual([m["id"] for m in res.json()["messages"]], ["p1"])
        _, kwargs = self.personal.users.return_value.messages.return_value.list.call_args
        self.assertEqual((kwargs["q"], kwargs["maxResults"]), ("from:boss", 1))
        self.school.users.return_value.messages.return_value.list.assert_not_called()

    def test_bad_account_400(self):
        self.assertEqual(self.client.get("/v1/inbox?account=Bad Label!").status_code, 400)

    def test_summary_failure_is_null(self):
        email_integration.get_important_unread.return_value = [{"from": "a", "subject": "b", "snippet": "c"}]
        with mock.patch.object(routes_chat, "get_client", side_effect=ValueError("No API key")):
            res = self.client.get("/v1/inbox")
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.json()["summary"])

    def test_summary_generated_and_cached(self):
        email_integration.get_important_unread.return_value = [{"from": "a", "subject": "b", "snippet": "c"}]
        fake = mock.MagicMock()
        fake.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Your boss needs the report.")])
        with mock.patch.object(routes_chat, "get_client", return_value=fake), \
                mock.patch.object(core, "load_app_config", return_value={}):
            first = self.client.get("/v1/inbox").json()["summary"]
            second = self.client.get("/v1/inbox").json()["summary"]
        self.assertEqual(first, "Your boss needs the report.")
        self.assertEqual(second, first)
        self.assertEqual(fake.messages.create.call_count, 1)

    def test_slow_summary_does_not_block(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        with mock.patch.object(routes_inbox, "SUMMARY_WAIT", 0.1), \
                mock.patch.object(routes_inbox, "generate_summary", side_effect=lambda: gate.wait(5) and None):
            start = time.monotonic()
            res = self.client.get("/v1/inbox")
            self.assertLess(time.monotonic() - start, 2)
        self.assertIsNone(res.json()["summary"])

    def test_thread(self):
        thread = {"thread_id": "t1", "account": "personal", "messages": [
            {"id": "m1", "from": "a", "to": "b", "date": "Mon", "subject": "Hello", "body": "Hi there"}]}
        with mock.patch.object(email_integration, "read_thread", return_value=thread) as read:
            res = self.client.get("/v1/inbox/personal/threads/t1")
        read.assert_called_once_with("t1", account="personal")
        self.assertEqual(res.json(), {"subject": "Hello", "messages": [
            {"id": "m1", "from": "a", "to": "b", "date": "Mon", "body_text": "Hi there"}]})

    def test_thread_missing_404(self):
        with mock.patch.object(email_integration, "read_thread", return_value=None):
            self.assertEqual(self.client.get("/v1/inbox/personal/threads/zz").status_code, 404)

    def test_actions(self):
        for action, fn in [("archive", "archive_email"), ("star", "star_email"), ("unstar", "unstar_email"),
                           ("read", "mark_read"), ("unread", "mark_unread"), ("trash", "trash_email")]:
            with mock.patch.object(email_integration, fn, return_value=True) as f:
                res = self.client.post("/v1/inbox/school/messages/m7/action", json={"action": action})
            self.assertEqual(res.json(), {"ok": True}, action)
            f.assert_called_once_with("m7", account="school")

    def test_unknown_action_400(self):
        res = self.client.post("/v1/inbox/school/messages/m7/action", json={"action": "explode"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"], "bad_action")

    def test_failed_action_is_an_error(self):
        with mock.patch.object(email_integration, "archive_email", return_value=False):
            res = self.client.post("/v1/inbox/school/messages/m7/action", json={"action": "archive"})
        self.assertEqual(res.status_code, 502)


if __name__ == "__main__":
    unittest.main()

"""notify.deliver, the FCM push, the app feed routes, and the senders that use them.

Run: python -m unittest discover -s tests -p 'test_*.py'
Everything happens in a temp dir; Signal and FCM are mocked, nothing is sent.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

import httpx

import notify
import paths


class TempDirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "credentials").mkdir()
        (root / "memory").mkdir()
        self.creds = root / "credentials"
        for name, value in (("CREDENTIALS_DIR", root / "credentials"), ("MEMORY_DIR", root / "memory")):
            p = mock.patch.object(paths, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def pair(self, *ids):
        (self.creds / "app_devices.json").write_text(json.dumps({"devices": [{"id": i} for i in ids]}))

    def fcm_key(self):
        (self.creds / "firebase_service_account.json").write_text(json.dumps({"project_id": "savvy-test"}))


class FeedTests(TempDirs):
    def test_records_and_pages_oldest_first(self):
        ids = [notify.record(f"t{i}", f"b{i}", "checkin")["id"] for i in range(5)]
        self.assertEqual([n["id"] for n in notify.feed()], ids)
        after = notify.feed(after_id=ids[1], limit=2)
        self.assertEqual([n["title"] for n in after], ["t2", "t3"])
        self.assertEqual(set(after[0]), {"id", "ts", "kind", "title", "body"})
        self.assertEqual(notify.feed(after_id=ids[-1]), [])

    def test_old_rows_are_pruned(self):
        with mock.patch.object(notify, "KEEP_ROWS", 3):
            for i in range(6):
                notify.record("t", str(i), "alert")
        self.assertEqual([n["body"] for n in notify.feed()], ["3", "4", "5"])


class FcmTests(TempDirs):
    def transport(self, dead=()):
        self.sent = []

        def handler(request: httpx.Request):
            msg = json.loads(request.content)["message"]
            self.sent.append((str(request.url), request.headers["authorization"], msg))
            if msg["token"] in dead:
                return httpx.Response(404, json={"error": {"code": 404, "status": "NOT_FOUND", "details": [
                    {"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": "UNREGISTERED"}]}})
            return httpx.Response(200, json={"name": "projects/savvy-test/messages/1"})

        return mock.patch.object(notify, "_http_client",
                                 lambda: httpx.Client(transport=httpx.MockTransport(handler)))

    def auth(self):
        return mock.patch.object(notify, "_fcm_auth", return_value=("savvy-test", "access-1"))

    def test_payload_shape_and_dead_token_pruning(self):
        self.pair("d1", "d2")
        self.fcm_key()
        notify.register_fcm_token("tok-a", "d1")
        notify.register_fcm_token("tok-b", "d2")
        with self.transport(dead={"tok-b"}), self.auth():
            ok = notify.deliver("Check-in", "How's the essay?", "checkin", {"signal_fallback": False})
        self.assertTrue(ok)
        url, authz, msg = self.sent[0]
        self.assertEqual(url, "https://fcm.googleapis.com/v1/projects/savvy-test/messages:send")
        self.assertEqual(authz, "Bearer access-1")
        self.assertNotIn("notification", msg)                       # data-only
        self.assertEqual(set(msg["data"]), {"id", "kind", "title", "body"})
        self.assertEqual(msg["data"]["kind"], "checkin")
        self.assertEqual(msg["data"]["id"], str(notify.feed()[0]["id"]))
        self.assertTrue(all(isinstance(v, str) for v in msg["data"].values()))
        self.assertEqual(notify.fcm_tokens(), ["tok-a"])
        mode = stat.S_IMODE(os.stat(self.creds / "app_fcm_tokens.json").st_mode)
        self.assertEqual(mode, 0o600)

    def test_long_body_is_clipped_and_flagged(self):
        msg = notify.fcm_message("t", {"id": 1, "kind": "weekly", "title": "W", "body": "é" * 5000})
        data = msg["message"]["data"]
        self.assertLessEqual(len(data["body"].encode()), notify.FCM_BODY_BYTES + 3)
        self.assertEqual(data["truncated"], "1")

    def test_one_token_per_device_and_unpaired_devices_get_nothing(self):
        self.pair("d1")
        notify.register_fcm_token("old", "d1")
        notify.register_fcm_token("new", "d1")
        notify.register_fcm_token("gone", "d9")
        self.assertEqual(notify.fcm_tokens(), ["new"])

    def test_no_service_account_means_no_push(self):
        self.pair("d1")
        notify.register_fcm_token("tok", "d1")
        with self.transport(), self.auth():
            notify.deliver("t", "b", "alert", {"signal_fallback": False})
        self.assertEqual(self.sent, [])


class DeliverTests(TempDirs):
    def deliver(self, config, signal_ok=True, **kw):
        with mock.patch("notifier.send_notification", return_value=signal_ok) as note, \
                mock.patch("notifier.send_message", return_value=signal_ok) as raw:
            ok = notify.deliver("Title", "Body", "reminder", config, **kw)
        return ok, note, raw

    def test_signal_fallback_on_by_default(self):
        ok, note, raw = self.deliver({})
        self.assertTrue(ok)
        note.assert_called_once_with("Title", "Body", {})
        raw.assert_not_called()

    def test_signal_text_is_sent_verbatim(self):
        _, note, raw = self.deliver({}, signal_text="🟢 whole message")
        raw.assert_called_once_with("🟢 whole message", {})
        note.assert_not_called()

    def test_signal_fallback_off(self):
        ok, note, _ = self.deliver({"signal_fallback": False})
        note.assert_not_called()
        self.assertFalse(ok)                      # nothing paired: nobody will see it
        self.assertEqual(len(notify.feed()), 1)   # but it's still recorded

    def test_recording_counts_once_a_phone_is_paired(self):
        self.pair("d1")
        ok, _, _ = self.deliver({"signal_fallback": False})
        self.assertTrue(ok)

    def test_signal_failure_without_a_phone_is_failure(self):
        ok, _, _ = self.deliver({}, signal_ok=False)
        self.assertFalse(ok)

    def test_unpaired_after_revoke(self):
        self.pair()
        self.assertFalse(notify.has_app_device())


class RouteTests(TempDirs):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from app_api.auth import require_device
        from app_api.server import create_app
        app = create_app()
        app.dependency_overrides[require_device] = lambda: {"id": "d1"}
        self.client = TestClient(app)
        self.pair("d1")

    def test_register_and_feed(self):
        r = self.client.post("/v1/devices/fcm", json={"fcm_token": "tok-1"})
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(notify.fcm_tokens(), ["tok-1"])
        for i in range(3):
            notify.record(f"t{i}", "b", "meal")
        first = notify.feed()[0]["id"]
        r = self.client.get("/v1/notifications", params={"after_id": first, "limit": 1})
        self.assertEqual([n["title"] for n in r.json()], ["t1"])
        self.assertEqual(self.client.post("/v1/devices/fcm", json={"fcm_token": " "}).status_code, 400)

    def test_requires_a_device(self):
        from app_api.server import create_app
        from fastapi.testclient import TestClient
        r = TestClient(create_app()).get("/v1/notifications")
        self.assertEqual(r.status_code, 401)


class LivenessTests(TempDirs):
    def test_importable_and_working_without_google(self):
        """Liveness reports broken installs, so it can't depend on them."""
        script = textwrap.dedent(f"""
            import sys
            for name in ("google", "google.oauth2", "google.auth", "googleapiclient", "anthropic"):
                sys.modules[name] = None
            sys.path.insert(0, {str(SRC)!r})
            from pathlib import Path
            import paths
            paths.CREDENTIALS_DIR = Path({str(self.creds)!r})
            paths.MEMORY_DIR = Path({str(self.creds.parent / 'memory')!r})
            import liveness, notify, notifier
            notifier.send_message = lambda *a, **k: True
            notify.register_fcm_token("tok", None)
            assert notify.deliver("t", "b", "alert", {{}}, signal_text="m") is True
            assert notify.feed()[0]["kind"] == "alert"
            print("ok")
        """)
        self.fcm_key()     # push is attempted and must fail softly without google-auth
        out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "ok", out.stderr)

    def test_alerts_go_through_deliver_with_their_kind(self):
        import liveness
        with mock.patch.object(liveness, "load_config", return_value={}), \
                mock.patch.object(liveness, "_check_cooldown", return_value=(True, 0)), \
                mock.patch.object(liveness, "_record_sent") as recorded, \
                mock.patch.object(liveness, "_systemctl_show", return_value={}), \
                mock.patch.object(liveness, "_journal_tail", return_value=""), \
                mock.patch.object(liveness, "deliver", return_value=True) as deliver:
            liveness.announce_up("Signal bot", "model x")
            liveness.announce_down("savvy-scheduler.service")
            liveness.announce_auth_needed("school", "expired", "link")
        kinds = [c.args[2] for c in deliver.call_args_list]
        self.assertEqual(kinds, ["alert", "alert", "signin"])
        self.assertEqual(deliver.call_args_list[0].args[0], "Signal bot is up")
        self.assertTrue(deliver.call_args_list[0].kwargs["signal_text"].startswith("🟢 Signal bot is up"))
        self.assertEqual(recorded.call_count, 3)


class SchedulerTests(TempDirs):
    @classmethod
    def setUpClass(cls):
        try:
            import scheduler
        except Exception as e:      # pragma: no cover - only when the app's deps are missing
            raise unittest.SkipTest(f"scheduler not importable: {e}")
        cls.scheduler = scheduler

    def test_do_send_passes_kind_and_keeps_the_daily_cap(self):
        s = self.scheduler
        state = {}
        with mock.patch.object(s, "save_state"), \
                mock.patch.object(s, "is_quiet_hours", return_value=False), \
                mock.patch.object(s.notify, "deliver", return_value=True) as deliver:
            cfg = {"max_notifications_per_day": 1}
            self.assertTrue(s.do_send("Rest day", "b", state, cfg, kind="rest"))
            self.assertFalse(s.do_send("Rest day", "b", state, cfg, kind="rest"))
        deliver.assert_called_once_with("Rest day", "b", "rest", cfg)
        self.assertEqual(state["notifications_today"], 1)

    def test_quiet_hours_send_nothing(self):
        s = self.scheduler
        with mock.patch.object(s, "is_quiet_hours", return_value=True), \
                mock.patch.object(s.notify, "deliver") as deliver:
            self.assertFalse(s.do_send("t", "b", {}, {}, kind="bod"))
        deliver.assert_not_called()

    def test_failed_delivery_is_not_counted(self):
        s = self.scheduler
        state = {}
        with mock.patch.object(s, "save_state"), \
                mock.patch.object(s, "is_quiet_hours", return_value=False), \
                mock.patch.object(s.notify, "deliver", return_value=False):
            self.assertFalse(s.do_send("t", "b", state, {}, kind="bod"))
        self.assertEqual(state.get("notifications_today", 0), 0)

    def test_each_sender_uses_its_kind(self):
        s = self.scheduler
        cases = {"bod_prompt": "bod", "eod_prompt": "eod"}
        for name, kind in cases.items():
            with mock.patch.object(s, "has_entry_today", return_value=False), \
                    mock.patch.object(s, "get_today_entries", return_value=[]), \
                    mock.patch.object(s, "save_state"), \
                    mock.patch.object(s, "CALENDAR_AVAILABLE", False):
                with mock.patch.object(s, "do_send", return_value=True) as send:
                    try:
                        getattr(s, name)({}, {})
                    except Exception as e:
                        self.fail(f"{name} raised {e}")
                self.assertEqual(send.call_args.kwargs.get("kind"), kind, name)

    def test_reminders_use_the_reminder_kind(self):
        s = self.scheduler
        with mock.patch.object(s, "pop_due_reminders", return_value=[{"id": 1, "instruction": "x"}]), \
                mock.patch.object(s, "autonomous_turn", return_value="done"), \
                mock.patch.object(s, "do_send", return_value=True) as send:
            s.run_due_reminders(None, None, {}, {})
        self.assertEqual(send.call_args.kwargs["kind"], "reminder")

    def test_every_do_send_call_names_a_valid_kind(self):
        import ast
        tree = ast.parse((SRC / "scheduler.py").read_text())
        kinds = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "do_send":
                kw = {k.arg: k.value for k in node.keywords}
                self.assertIn("kind", kw, ast.unparse(node))
                kinds.append(kw["kind"].value)
        self.assertEqual(sorted(kinds), sorted(
            ["bod", "eod", "checkin", "weekly", "meal", "fact_check", "rest", "reminder"]))
        self.assertTrue(set(kinds) <= set(notify.KINDS))


if __name__ == "__main__":
    unittest.main()

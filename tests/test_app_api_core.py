"""App server core: pairing, auth, status, settings, memory, Google connect.

Run: python -m unittest discover -s tests -p 'test_*.py'

Every path points at a temp directory and every Google call is mocked; the real
credentials are never read or touched.
"""

import json
import shutil
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi.testclient import TestClient

import google_auth as ga
import memory
import paths
import tool_diagnostics
from app_api import auth, routes_core, routes_google
from app_api.server import create_app


class ApiCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patches = [
            mock.patch.object(paths, "CREDENTIALS_DIR", self.tmp),
            mock.patch.object(paths, "CONFIG_PATH", self.tmp / "config.json"),
            mock.patch.object(ga, "BASE_DIR", self.tmp),
            mock.patch.object(ga, "SIGNIN_DIR", self.tmp / "signin_pending"),
            mock.patch.object(ga, "CREDENTIALS_FILE", self.tmp / "credentials.json"),
            mock.patch.object(ga, "_needs_signin", {}),
            mock.patch.object(memory, "DB_PATH", self.tmp / "memory.db"),
            mock.patch.object(tool_diagnostics, "CONFIG_PATH", self.tmp / "tool_config.json"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(create_app())

    def pair(self, name="Pixel 8") -> str:
        code = auth.new_pairing_code()
        r = self.client.post("/v1/pair", json={"code": code, "device_name": name})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["token"]

    def authed(self) -> dict:
        return {"Authorization": f"Bearer {self.pair()}"}


class PairingTests(ApiCase):
    def test_ping_is_public(self):
        r = self.client.get("/v1/ping")
        self.assertEqual(r.json(), {"ok": True, "name": "Savvy", "version": "1"})

    def test_good_code_gives_working_token_stored_hashed(self):
        token = self.pair()
        devices = self.tmp / "app_devices.json"
        self.assertNotIn(token, devices.read_text())
        self.assertEqual(stat.S_IMODE(devices.stat().st_mode), 0o600)
        self.assertEqual(json.loads(devices.read_text())["devices"][0]["name"], "Pixel 8")
        r = self.client.get("/v1/settings", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 200)

    def test_code_is_single_use(self):
        code = auth.new_pairing_code()
        self.assertEqual(self.client.post("/v1/pair", json={"code": code}).status_code, 200)
        r = self.client.post("/v1/pair", json={"code": code})
        self.assertEqual((r.status_code, r.json()["error"]), (403, "bad_code"))

    def test_wrong_code(self):
        code = auth.new_pairing_code()
        wrong = f"{(int(code) + 1) % 10**6:06d}"
        r = self.client.post("/v1/pair", json={"code": wrong})
        self.assertEqual((r.status_code, r.json()["error"]), (403, "bad_code"))

    def test_expired_code(self):
        code = auth.new_pairing_code()
        later = time.time() + auth.CODE_TTL + 1
        with mock.patch("app_api.auth.time.time", return_value=later):
            r = self.client.post("/v1/pair", json={"code": code})
        self.assertEqual(r.status_code, 403)

    def test_rate_limit_burns_the_code(self):
        code = auth.new_pairing_code()
        wrong = f"{(int(code) + 1) % 10**6:06d}"
        for _ in range(auth.MAX_BAD_TRIES):
            self.assertEqual(self.client.post("/v1/pair", json={"code": wrong}).status_code, 403)
        r = self.client.post("/v1/pair", json={"code": code})
        self.assertEqual((r.status_code, r.json()["error"]), (429, "too_many_attempts"))
        # A fresh code from the PC starts over.
        self.pair()

    def test_protected_route_needs_token(self):
        for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic x"}):
            r = self.client.get("/v1/status", headers=headers)
            self.assertEqual((r.status_code, r.json()["error"]), (401, "unpaired"))

    def test_revoke_all(self):
        headers = self.authed()
        self.assertEqual(auth.revoke_all_devices(), 1)
        self.assertEqual(self.client.get("/v1/settings", headers=headers).status_code, 401)


class SettingsTests(ApiCase):
    def test_round_trip_keeps_other_keys(self):
        paths.CONFIG_PATH.write_text(json.dumps(
            {"anthropic_api_key": "k", "rest_days": {"check_time": "19:00"}, "bod_time": "07:00"}))
        h = self.authed()
        self.assertEqual(self.client.get("/v1/settings", headers=h).json()["bod_time"], "07:00")
        r = self.client.put("/v1/settings", headers=h, json={
            "eod_time": "21:30", "checkin_times": ["13:00", "09:30"], "weekly_review_day": "friday",
            "quiet_hours_start": 22, "rest_day_warnings": False, "signal_fallback": False})
        self.assertEqual(r.status_code, 200, r.text)
        got = r.json()
        self.assertEqual(got["checkin_times"], ["09:30", "13:00"])
        self.assertEqual(got["weekly_review_day"], "Friday")
        self.assertFalse(got["rest_day_warnings"])
        saved = json.loads(paths.CONFIG_PATH.read_text())
        self.assertEqual(saved["anthropic_api_key"], "k")
        self.assertEqual(saved["bod_time"], "07:00")
        self.assertEqual(saved["rest_days"], {"check_time": "19:00", "enabled": False})
        self.assertEqual(self.client.get("/v1/settings", headers=h).json(), got)

    def test_rejects_bad_values_and_unknown_keys(self):
        paths.CONFIG_PATH.write_text("{}")
        h = self.authed()
        for body in ({"bod_time": "8:30"}, {"bod_time": "24:00"}, {"quiet_hours_end": 24},
                     {"quiet_hours_start": "7"}, {"weekly_review_day": "Funday"},
                     {"checkin_times": "09:00"}, {"signal_fallback": "yes"}):
            r = self.client.put("/v1/settings", headers=h, json=body)
            self.assertEqual((r.status_code, r.json()["error"]), (400, "bad_value"), body)
        r = self.client.put("/v1/settings", headers=h, json={"anthropic_api_key": "x"})
        self.assertEqual((r.status_code, r.json()["error"]), (400, "unknown_setting"))
        self.assertEqual(json.loads(paths.CONFIG_PATH.read_text()), {})


class MemoryAndLevelsTests(ApiCase):
    def test_facts_search_and_delete(self):
        m = memory.Memory(session_id="t")
        with mock.patch.object(memory, "_embed", return_value=[]):
            m.store_fact("likes oat milk", "stable")
            m.store_fact("exam on Friday", "ephemeral")
        m.close()
        h = self.authed()
        facts = self.client.get("/v1/memory/facts", headers=h, params={"q": "oat"}).json()
        self.assertEqual([(f["fact"], f["type"]) for f in facts], [("likes oat milk", "stable")])
        self.assertIn("T", facts[0]["updated"])
        fid = facts[0]["id"]
        self.assertEqual(self.client.delete(f"/v1/memory/facts/{fid}", headers=h).json(), {"ok": True})
        self.assertEqual(self.client.delete(f"/v1/memory/facts/{fid}", headers=h).status_code, 404)
        self.assertEqual(len(self.client.get("/v1/memory/facts", headers=h).json()), 1)

    def test_confirm_levels(self):
        tool_diagnostics.CONFIG_PATH.write_text(json.dumps({"danger_levels": {"send_email": 2, "list_events": 0}}))
        r = self.client.get("/v1/confirm-levels", headers=self.authed())
        self.assertEqual(r.json(), {"list_events": 0, "send_email": 2})


class StatusTests(ApiCase):
    def test_status(self):
        paths.CONFIG_PATH.write_text(json.dumps({"todoist_api_token": "t", "signal_fallback": False}))
        for label, email in (("personal", "a@x.com"), ("school", "b@y.edu")):
            (self.tmp / f"token_{label}.json").write_text(json.dumps({"account": email}))
        good = mock.Mock(valid=True)

        def creds(label, interactive=None):
            self.assertFalse(interactive)
            if label == "school":
                ga._needs_signin[label] = "the login expired or was revoked"
                return None
            return good

        with mock.patch.object(ga, "get_credentials", side_effect=creds), \
             mock.patch.object(routes_core, "_ollama_up", return_value=True):
            r = self.client.get("/v1/status", headers=self.authed())
        self.assertEqual(r.json(), {
            "google": [
                {"label": "personal", "email": "a@x.com", "connected": True, "reason": None},
                {"label": "school", "email": "b@y.edu", "connected": False,
                 "reason": "the login expired or was revoked"}],
            "todoist": True, "ollama": True, "signal_fallback": False})


class GoogleTests(ApiCase):
    def write_web_client(self):
        (self.tmp / "web_client.json").write_text(json.dumps({"web": {
            "client_id": "abc.apps.googleusercontent.com", "client_secret": "s",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"}}))

    def connect(self, flow, email, label="school"):
        with mock.patch.object(routes_google, "Flow") as F, \
             mock.patch.object(ga, "fetch_account_email", return_value=email):
            F.from_client_secrets_file.return_value = flow
            return self.client.post("/v1/google/connect", headers=self.h,
                                    json={"label": label, "server_auth_code": "4/code"})

    def fake_flow(self):
        flow = mock.Mock()
        flow.credentials.to_json.return_value = json.dumps({"token": "a", "refresh_token": "r"})
        return flow

    def setUp(self):
        super().setUp()
        self.h = self.authed()

    def test_config_missing_is_503(self):
        r = self.client.get("/v1/google/config", headers=self.h)
        self.assertEqual((r.status_code, r.json()["error"]), (503, "no_web_client"))

    def test_config(self):
        self.write_web_client()
        r = self.client.get("/v1/google/config", headers=self.h).json()
        self.assertEqual(r, {"web_client_id": "abc.apps.googleusercontent.com", "scopes": ga.SCOPES})

    def test_connect_success_saves_token(self):
        self.write_web_client()
        ga._needs_signin["school"] = "no saved login"
        flow = self.fake_flow()
        r = self.connect(flow, "b@y.edu")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["email"], "b@y.edu")
        flow.fetch_token.assert_called_once_with(code="4/code")
        saved = json.loads((self.tmp / "token_school.json").read_text())
        self.assertEqual((saved["account"], saved["refresh_token"]), ("b@y.edu", "r"))
        self.assertNotIn("school", ga.signin_needed())
        # Disconnect removes it again.
        self.assertEqual(self.client.delete("/v1/google/school", headers=self.h).json(), {"ok": True})
        self.assertFalse((self.tmp / "token_school.json").exists())

    def test_wrong_account(self):
        self.write_web_client()
        (self.tmp / "token_school.json").write_text(json.dumps({"account": "b@y.edu"}))
        r = self.connect(self.fake_flow(), "a@x.com")
        self.assertEqual((r.status_code, r.json()["error"]), (409, "wrong_account"))
        self.assertEqual(json.loads((self.tmp / "token_school.json").read_text()), {"account": "b@y.edu"})

    def test_label_clash(self):
        self.write_web_client()
        (self.tmp / "token_personal.json").write_text(json.dumps({"account": "a@x.com"}))
        r = self.connect(self.fake_flow(), "A@x.com")
        self.assertEqual((r.status_code, r.json()["error"]), (409, "label_clash"))
        self.assertFalse((self.tmp / "token_school.json").exists())

    def test_exchange_failure(self):
        self.write_web_client()
        flow = self.fake_flow()
        flow.fetch_token.side_effect = ValueError("invalid_grant")
        r = self.connect(flow, "b@y.edu")
        self.assertEqual((r.status_code, r.json()["error"]), (400, "exchange_failed"))

    def test_partial_scopes(self):
        self.write_web_client()
        flow = self.fake_flow()
        w = Warning("Scope has changed")
        w.token, w.new_scope = {"access_token": "a"}, ga.SCOPES[:1]
        flow.fetch_token.side_effect = w
        r = self.connect(flow, "b@y.edu")
        self.assertEqual((r.status_code, r.json()["error"]), (400, "exchange_failed"))

    def test_bad_label(self):
        self.write_web_client()
        r = self.connect(self.fake_flow(), "b@y.edu", label="../x")
        self.assertEqual((r.status_code, r.json()["error"]), (400, "bad_label"))
        r = self.client.delete("/v1/google/BAD", headers=self.h)
        self.assertEqual(r.status_code, 400)

    def test_no_web_client_on_connect(self):
        r = self.connect(self.fake_flow(), "b@y.edu")
        self.assertEqual(r.status_code, 503)


if __name__ == "__main__":
    unittest.main()

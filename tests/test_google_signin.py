"""Google sign-in links. Run: python -m unittest discover tests

Runs a real token exchange against a local stand-in for Google's token endpoint,
which checks the PKCE proof, so a link and its later completion must truly match.
Everything lives in a temp directory; the real credentials are never touched.
"""

import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"   # the stand-in token endpoint is plain http

from google.auth.exceptions import RefreshError, TransportError

import core
import google_auth as ga
import liveness


class FakeGoogle(BaseHTTPRequestHandler):
    """Token endpoint. Only accepts the code it was told to, with a matching verifier."""
    challenge = ""
    scope = " ".join(ga.SCOPES)

    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        verifier = form.get("code_verifier", [""])[0]
        digest = hashlib.sha256(verifier.encode()).digest()
        made = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        good = (made == type(self).challenge and form["code"] == ["good-code"]
                and form["redirect_uri"] == [f"http://localhost:{ga.SIGNIN_PORT}/"])
        body = {"access_token": "new-access", "refresh_token": "new-refresh", "token_type": "Bearer",
                "expires_in": 3600, "scope": type(self).scope} if good else {"error": "invalid_grant"}
        self.send_response(200 if good else 400)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args):
        pass


class SigninCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.google = HTTPServer(("127.0.0.1", 0), FakeGoogle)
        threading.Thread(target=cls.google.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.google.shutdown()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        client = self.dir / "credentials.json"
        client.write_text(json.dumps({"installed": {
            "client_id": "cid.apps.googleusercontent.com", "client_secret": "secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": f"http://127.0.0.1:{self.google.server_port}/token",
            "redirect_uris": ["http://localhost"],
        }}))
        for patcher in (
            mock.patch.object(ga, "BASE_DIR", self.dir),
            mock.patch.object(ga, "CREDENTIALS_FILE", client),
            mock.patch.object(ga, "SIGNIN_DIR", self.dir / "signin_pending"),
            mock.patch.object(ga, "_needs_signin", {}),
            mock.patch.object(ga, "start_signin_listener"),
            mock.patch.object(ga, "fetch_account_email", return_value="me@example.com"),
            mock.patch.object(core, "_last_signin_notice", None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        FakeGoogle.scope = " ".join(ga.SCOPES)

    def save_token(self, label="personal", account="me@example.com"):
        (self.dir / f"token_{label}.json").write_text(json.dumps({
            "token": "old", "refresh_token": "old-refresh", "client_id": "cid", "client_secret": "s",
            "token_uri": "http://127.0.0.1:1/none", "scopes": ga.SCOPES,
            "expiry": "2020-01-01T00:00:00Z", "account": account,
        }))

    def make_link(self, label="personal"):
        url = ga.signin_link(label)
        query = parse_qs(urlsplit(url).query)
        FakeGoogle.challenge = query["code_challenge"][0]
        return url, query

    def redirect(self, query, code="good-code"):
        return ("http://localhost:38917/?" +
                urlencode({"state": query["state"][0], "code": code, "scope": " ".join(ga.SCOPES)}))


class LinkTests(SigninCase):
    def test_link_asks_for_a_lasting_login_on_the_right_account(self):
        self.save_token()
        _, q = self.make_link()
        self.assertEqual(q["access_type"], ["offline"])
        self.assertEqual(q["prompt"], ["consent"])
        self.assertEqual(q["login_hint"], ["me@example.com"])
        self.assertEqual(q["redirect_uri"], [f"http://localhost:{ga.SIGNIN_PORT}/"])

    def test_repeat_requests_reuse_the_link_until_it_gets_old(self):
        first, q = self.make_link()
        self.assertEqual(ga.signin_link("personal"), first)
        self.assertNotEqual(ga.signin_link("school"), first)

        path = ga._pending_path(q["state"][0])
        data = json.loads(path.read_text())
        data["created"] = time.time() - ga.SIGNIN_REUSE_AGE - 1
        path.write_text(json.dumps(data))
        self.assertNotEqual(ga.signin_link("personal"), first)
        self.assertTrue(path.exists(), "a link already sent should keep working")

        data["created"] = time.time() - ga.SIGNIN_MAX_AGE - 1
        path.write_text(json.dumps(data))
        ga.signin_link("personal")
        self.assertFalse(path.exists())

    def test_pending_file_is_private(self):
        if sys.platform == "win32":
            self.skipTest("POSIX permissions")
        _, q = self.make_link()
        self.assertEqual(ga._pending_path(q["state"][0]).stat().st_mode & 0o777, 0o600)

    def test_state_cannot_escape_the_pending_folder(self):
        for state in ("../../credentials", "a/b", "", "short"):
            self.assertIsNone(ga._pending_path(state))


class CompleteTests(SigninCase):
    def test_pasted_address_signs_in_and_saves_token(self):
        self.save_token()
        _, q = self.make_link()
        reply = ga.finish_signin_from_text(self.redirect(q))
        self.assertIn("signed in again as me@example.com", reply)
        token = json.loads((self.dir / "token_personal.json").read_text())
        self.assertEqual(token["refresh_token"], "new-refresh")
        self.assertEqual(token["account"], "me@example.com")
        self.assertFalse(list(ga.SIGNIN_DIR.glob("*.json")))
        self.assertEqual(ga.finish_signin_from_text(self.redirect(q))[:2], "⚠️")

    def test_address_copied_without_scheme_or_inside_a_sentence(self):
        _, q = self.make_link()
        bare = self.redirect(q).removeprefix("http://")
        self.assertIn("signed in", ga.finish_signin_from_text(f"here you go {bare}"))

    def test_clears_the_flag_and_the_notice(self):
        ga._needs_signin["personal"] = "expired"
        _, q = self.make_link()
        ga.finish_signin_from_text(self.redirect(q))
        self.assertEqual(ga.signin_notice(), "")

    def test_wrong_account_is_refused(self):
        self.save_token(account="someone-else@example.com")
        before = (self.dir / "token_personal.json").read_text()
        _, q = self.make_link()
        reply = ga.finish_signin_from_text(self.redirect(q))
        self.assertIn("Nothing was saved", reply)
        self.assertEqual((self.dir / "token_personal.json").read_text(), before)

    def test_account_saved_under_another_label_is_refused(self):
        self.save_token("school", "me@example.com")
        _, q = self.make_link("personal")
        self.assertIn("already saved as 'school'", ga.finish_signin_from_text(self.redirect(q)))
        self.assertFalse((self.dir / "token_personal.json").exists())

    def test_bad_code_keeps_the_link_and_old_token(self):
        self.save_token()
        _, q = self.make_link()
        self.assertIn("Try the link once more", ga.finish_signin_from_text(self.redirect(q, "bad")))
        self.assertEqual(json.loads((self.dir / "token_personal.json").read_text())["token"], "old")
        self.assertTrue(ga._pending_path(q["state"][0]).exists())

    def test_partial_permissions_are_explained_and_not_saved(self):
        FakeGoogle.scope = ga.SCOPES[0]
        _, q = self.make_link()
        self.assertIn("every box ticked", ga.finish_signin_from_text(self.redirect(q)))
        self.assertFalse((self.dir / "token_personal.json").exists())

    def test_ordinary_messages_are_left_alone(self):
        for text in ("what's on today?", "the app is at localhost:8080", "http://localhost:8080/?page=2",
                     "http://localhost:3000/?state=open"):
            self.assertIsNone(ga.finish_signin_from_text(text), text)

    def test_cancelled_sign_in(self):
        self.assertIn("cancelled", ga.finish_signin_from_text(
            "http://localhost:38917/?state=abcdefghijklmnopqrst&error=access_denied"))


class ListenerTests(SigninCase):
    def setUp(self):
        super().setUp()
        self.server = ga._SigninServer(("127.0.0.1", 0), ga._SigninHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)

    def get(self, query):
        try:
            with urlopen(f"http://127.0.0.1:{self.server.server_port}/?{query}") as r:
                return r.status, r.read().decode()
        except HTTPError as e:
            return e.code, e.read().decode()

    def test_finishes_the_sign_in(self):
        _, q = self.make_link()
        status, page = self.get(urlsplit(self.redirect(q)).query)
        self.assertEqual(status, 200)
        self.assertIn("You're all set", page)
        self.assertTrue((self.dir / "token_personal.json").exists())

    def test_unknown_state_and_stray_requests_save_nothing(self):
        self.assertEqual(self.get("state=abcdefghijklmnopqrst&code=x")[0], 400)
        self.assertEqual(self.get("")[0], 404)
        self.assertEqual(self.get("state=<script>")[0], 400)
        self.assertFalse(list(self.dir.glob("token_*")))


class FailureDetectionTests(SigninCase):
    def test_revoked_login_alerts_with_a_link_and_is_not_interactive(self):
        self.save_token()
        with mock.patch("google_auth.Credentials.refresh", side_effect=RefreshError("invalid_grant")), \
                mock.patch("liveness.announce_auth_needed") as announce, \
                mock.patch.object(ga, "InstalledAppFlow") as browser:
            self.assertIsNone(ga.get_credentials("personal", interactive=False))
        browser.from_client_secrets_file.assert_not_called()
        label, reason, fix = announce.call_args.args
        self.assertEqual(label, "personal")
        self.assertEqual(reason, "the login expired or was revoked")
        self.assertIn("https://accounts.google.com/", fix)
        self.assertIn("personal", ga.signin_needed())

    def test_network_trouble_does_not_ask_for_a_sign_in(self):
        self.save_token()
        with mock.patch("google_auth.Credentials.refresh", side_effect=TransportError("offline")), \
                mock.patch("liveness.announce_auth_needed") as announce:
            self.assertIsNone(ga.get_credentials("personal", interactive=False))
        announce.assert_not_called()
        self.assertEqual(ga.signin_needed(), {})

    def test_unreadable_token_asks_for_a_sign_in_instead_of_crashing(self):
        (self.dir / "token_personal.json").write_text("{not json")
        with mock.patch("liveness.announce_auth_needed") as announce:
            self.assertIsNone(ga.get_credentials("personal", interactive=False))
        announce.assert_called_once()

    def test_broken_client_file_still_warns(self):
        self.save_token()
        ga.CREDENTIALS_FILE.write_text("{}")
        with mock.patch("google_auth.Credentials.refresh", side_effect=RefreshError("x")), \
                mock.patch("liveness.announce_auth_needed") as announce:
            ga.get_credentials("personal", interactive=False)
        self.assertEqual(announce.call_args.args[2], "")

    def test_unattended_service_at_a_terminal_never_opens_the_browser(self):
        self.save_token()
        with mock.patch.dict(os.environ, {"SAVVY_UNATTENDED": "1"}), \
                mock.patch("sys.stdin.isatty", return_value=True), \
                mock.patch("google_auth.Credentials.refresh", side_effect=RefreshError("x")), \
                mock.patch("liveness.announce_auth_needed"), \
                mock.patch.object(ga, "InstalledAppFlow") as browser:
            ga.get_credentials("personal")
        browser.from_client_secrets_file.assert_not_called()


class AlertTests(unittest.TestCase):
    def send(self, *args):
        with mock.patch.object(liveness, "load_config", return_value={}), \
                mock.patch.object(liveness, "_enabled", return_value=True), \
                mock.patch.object(liveness, "_check_cooldown", return_value=(True, 0)), \
                mock.patch.object(liveness, "_record_sent"), \
                mock.patch.object(liveness, "send_message", return_value=True) as send:
            liveness.announce_auth_needed(*args)
        return send.call_args.args[0]

    def test_alert_carries_the_link(self):
        msg = self.send("personal", "expired", "Sign in:\nhttps://accounts.google.com/x")
        self.assertIn("https://accounts.google.com/x", msg)
        self.assertNotIn("google_auth.py", msg)

    def test_without_a_link_it_falls_back_to_the_command(self):
        self.assertIn("google_auth.py personal", self.send("personal", "expired"))


class ReplyTests(SigninCase):
    def test_nothing_added_when_signed_in(self):
        self.assertEqual(core.with_signin_notice("Hello"), "Hello")

    def test_signal_gets_a_bare_link_and_the_phone_tip(self):
        ga._needs_signin["personal"] = "expired"
        out = core.with_signin_notice("Hello")
        self.assertRegex(out, r"(?m)^https://accounts\.google\.com/\S+$")
        self.assertIn("copy its address", out)

    def test_desktop_gets_a_clickable_markdown_link(self):
        ga._needs_signin["personal"] = "expired"
        out = core.with_signin_notice("Hello", markdown=True)
        self.assertRegex(out, r"\[Sign in to Google: personal\]\(https://accounts\.google\.com/\S+\)")
        self.assertNotIn("copy its address", out)

    def test_not_on_every_reply_unless_the_reply_brings_it_up(self):
        ga._needs_signin["personal"] = "expired"
        self.assertIn("accounts.google", core.with_signin_notice("first"))
        self.assertEqual(core.with_signin_notice("second"), "second")
        self.assertIn("accounts.google", core.with_signin_notice("You need to sign back in."))
        self.assertIn("accounts.google", core.with_signin_notice("Your login needs re-auth"))
        with mock.patch.object(core, "_last_signin_notice", time.monotonic() - core.SIGNIN_NOTICE_EVERY):
            self.assertIn("accounts.google", core.with_signin_notice("an hour later"))

    def test_fits_signal_without_cutting_the_link(self):
        from notifier import split_message
        ga._needs_signin.update(personal="expired", school="expired")
        url = ga.signin_link("school")
        parts = split_message(core.with_signin_notice("x " * 650))
        self.assertTrue(any(url in p for p in parts))


if __name__ == "__main__":
    unittest.main()

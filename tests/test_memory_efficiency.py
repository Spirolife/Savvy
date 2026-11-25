"""Reused Google clients and the tray's memory watch. Run: python -m unittest discover tests

Nothing here calls Google: the clients are built from the library's bundled API
descriptions with throwaway credentials and never make a request.
"""

import os
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import google_auth as ga


def creds(token):
    return SimpleNamespace(token=token, universe_domain="googleapis.com")


class CachedServiceTests(unittest.TestCase):
    def setUp(self):
        ga._service_cache.__dict__.clear()
        patcher = mock.patch("googleapiclient.discovery.build", side_effect=lambda *a, **k: object())
        self.build = patcher.start()
        self.addCleanup(patcher.stop)

    def test_reused_while_the_token_is_the_same(self):
        a = ga.cached_service("gmail", "v1", "personal", creds("t1"))
        b = ga.cached_service("gmail", "v1", "personal", creds("t1"))
        self.assertIs(a, b)
        self.assertEqual(self.build.call_count, 1)

    def test_new_token_builds_a_new_client(self):
        a = ga.cached_service("gmail", "v1", "personal", creds("t1"))
        b = ga.cached_service("gmail", "v1", "personal", creds("t2"))
        self.assertIsNot(a, b)
        self.assertIs(b, ga.cached_service("gmail", "v1", "personal", creds("t2")))

    def test_accounts_and_apis_are_separate(self):
        gmail = ga.cached_service("gmail", "v1", "personal", creds("t"))
        school = ga.cached_service("gmail", "v1", "school", creds("t"))
        calendar = ga.cached_service("calendar", "v3", "personal", creds("t"))
        self.assertEqual(len({id(gmail), id(school), id(calendar)}), 3)

    def test_each_thread_gets_its_own_client(self):
        mine = ga.cached_service("gmail", "v1", "personal", creds("t"))
        theirs = []
        t = threading.Thread(target=lambda: theirs.append(ga.cached_service("gmail", "v1", "personal", creds("t"))))
        t.start(); t.join()
        self.assertIsNot(mine, theirs[0])

    def test_email_and_calendar_use_the_cache(self):
        import calendar_integration
        import email_integration
        accounts = {"personal": creds("t")}
        with mock.patch.object(email_integration, "get_all_credentials", return_value=accounts), \
                mock.patch.object(calendar_integration, "get_all_credentials", return_value=accounts):
            first = email_integration._get_services()[0][1]
            self.assertIs(first, email_integration._get_services()[0][1])
            cal = calendar_integration._get_services()[0][1]
            self.assertIs(cal, calendar_integration._get_services()[0][1])
        self.assertEqual(self.build.call_count, 2)


try:
    import desktop
except Exception:       # no PySide6 or no display libraries: skip the tray tests
    desktop = None


@unittest.skipIf(desktop is None, "the tray app can't be imported here")
class MemoryWatchTests(unittest.TestCase):
    def test_reads_this_process(self):
        mb = desktop.process_memory_mb(os.getpid())
        self.assertIsNotNone(mb)
        self.assertGreater(mb, 1)

    def test_unknown_process_is_none(self):
        with mock.patch.dict(sys.modules, {"psutil": None}):
            self.assertIsNone(desktop.process_memory_mb(2 ** 30))

    def watch(self, readings, limit=800, paused=False):
        services = [SimpleNamespace(label=label, memory_mb=lambda mb=mb: mb, stop=mock.Mock(), start=mock.Mock())
                    for label, mb in readings]
        tray = SimpleNamespace(services=services, paused=paused, tray=mock.Mock())
        logged = []
        with mock.patch.object(desktop, "memory_limit_mb", return_value=limit), \
                mock.patch.object(desktop, "log_memory", side_effect=logged.append):
            desktop.TrayApp.check_memory(tray)
        return services, tray, logged

    def test_logs_every_service_and_restarts_only_the_big_one(self):
        services, tray, logged = self.watch([("Signal bot", 950.0), ("Scheduler", 120.0), ("App server", None)])
        self.assertRegex(logged[0], r"^MB  Signal bot 950  Scheduler 120  Tray \d+$")
        services[0].stop.assert_called_once(); services[0].start.assert_called_once()
        services[1].stop.assert_not_called()
        self.assertIn("Signal bot was using 950 MB", logged[1])
        tray.tray.showMessage.assert_called_once()

    def heartbeat_service(self, beat_age, started_age=3600):
        import tempfile, time
        path = Path(tempfile.mkdtemp()) / "heartbeat"
        path.touch()
        now = time.time()
        os.utime(path, (now - beat_age, now - beat_age))
        return SimpleNamespace(label="Scheduler", heartbeat=path, heartbeat_stale=False, running=True,
                               started_at=now - started_age, stop=mock.Mock(), start=mock.Mock())

    def run_heartbeat_check(self, service, paused=False):
        tray = SimpleNamespace(services=[service], paused=paused, tray=mock.Mock())
        with mock.patch.object(desktop, "log_memory"):
            desktop.TrayApp.check_heartbeats(tray)
        return tray

    def test_stuck_loop_restarts_on_the_second_stale_check(self):
        s = self.heartbeat_service(beat_age=30 * 60)
        self.run_heartbeat_check(s)
        s.stop.assert_not_called()          # first sighting: maybe the PC just woke up
        tray = self.run_heartbeat_check(s)
        s.stop.assert_called_once(); s.start.assert_called_once()
        self.assertIn("stopped responding", tray.tray.showMessage.call_args[0][1])

    def test_fresh_beat_in_between_clears_it(self):
        s = self.heartbeat_service(beat_age=30 * 60)
        self.run_heartbeat_check(s)
        os.utime(s.heartbeat)               # beat again, as a live loop does after waking
        self.run_heartbeat_check(s)
        s.stop.assert_not_called()

    def test_no_restart_before_the_first_beat_or_when_paused(self):
        starting = self.heartbeat_service(beat_age=30 * 60, started_age=60)   # last beat predates this start
        for _ in range(2):
            self.run_heartbeat_check(starting)
        starting.stop.assert_not_called()
        paused = self.heartbeat_service(beat_age=30 * 60)
        for _ in range(2):
            self.run_heartbeat_check(paused, paused=True)
        paused.stop.assert_not_called()

    def test_no_restart_when_off_or_paused(self):
        for kwargs in ({"limit": 0}, {"paused": True}):
            services, tray, logged = self.watch([("Signal bot", 5000.0)], **kwargs)
            services[0].stop.assert_not_called()
            self.assertEqual(len(logged), 1)


if __name__ == "__main__":
    unittest.main()

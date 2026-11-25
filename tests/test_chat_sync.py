"""Catching up on messages from other chat surfaces. Run: python -m unittest discover tests

Uses a temporary memory database and no embeddings (Ollama isn't called).
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import memory


class RetrieveOthersSinceTests(unittest.TestCase):
    def setUp(self):
        db = Path(tempfile.mkdtemp()) / "memory.db"
        for p in (mock.patch.object(memory, "DB_PATH", db), mock.patch.object(memory, "_embed", return_value=None)):
            p.start()
            self.addCleanup(p.stop)

    def store(self, session, role, content, ts):
        m = memory.Memory(session_id=session)
        with mock.patch.object(memory.time, "time", return_value=ts):
            m.store(role, content)
        m.db.close()

    def test_only_other_sessions_after_the_mark_oldest_first(self):
        self.store("desktop", "user", "old desk", 100.0)
        self.store("app", "user", "[App] mine", 200.0)
        self.store("desktop", "user", "new desk", 300.0)
        self.store("scheduler", "assistant", "check-in", 400.0)
        app = memory.Memory(session_id="app")
        got = app.retrieve_others_since(150.0)
        app.db.close()
        self.assertEqual([(r["content"], r["timestamp"]) for r in got], [("new desk", 300.0), ("check-in", 400.0)])

    def test_limit_keeps_the_newest(self):
        for i in range(5):
            self.store("signal_bot", "user", f"m{i}", 10.0 + i)
        desk = memory.Memory(session_id="desktop")
        got = desk.retrieve_others_since(0.0, n=2)
        desk.db.close()
        self.assertEqual([r["content"] for r in got], ["m3", "m4"])


try:
    import desktop
except Exception:       # no PySide6 or no display libraries
    desktop = None


@unittest.skipIf(desktop is None, "the tray app can't be imported here")
class DesktopCatchUpTests(unittest.TestCase):
    def window(self, seen_ts):
        from types import SimpleNamespace
        w = SimpleNamespace(messages=[("you", "hi"), ("savvy", "hello")], seen_ts=seen_ts,
                            _render=mock.Mock(), view=mock.MagicMock())
        w._history_entries = desktop.ChatWindow._history_entries
        w._note_seen = lambda rows: desktop.ChatWindow._note_seen(w, rows)
        return w

    def test_labels_each_surface_and_strips_its_tag(self):
        rows = [{"role": "user", "content": "[App] from the phone"},
                {"role": "assistant", "content": "[Signal] by text"},
                {"role": "assistant", "content": "check-in"}]
        self.assertEqual(desktop.ChatWindow._history_entries(rows),
                         [("you:app", "from the phone"), ("savvy:signal", "by text"), ("savvy", "check-in")])

    def test_caught_up_appends_only_unseen_messages(self):
        w = self.window(seen_ts=100.0)
        desktop.ChatWindow._on_caught_up(w, [
            {"role": "user", "content": "[App] old", "timestamp": 90.0},
            {"role": "user", "content": "[App] laundry?", "timestamp": 110.0},
            {"role": "assistant", "content": "[App] plan", "timestamp": 111.0},
        ])
        self.assertEqual(w.messages[-2:], [("you:app", "laundry?"), ("savvy:app", "plan")])
        self.assertEqual(len(w.messages), 4)
        self.assertEqual(w.seen_ts, 111.0)
        w._render.assert_called_once()

    def test_nothing_new_changes_nothing(self):
        w = self.window(seen_ts=200.0)
        desktop.ChatWindow._on_caught_up(w, [{"role": "user", "content": "x", "timestamp": 150.0}])
        self.assertEqual(len(w.messages), 2)
        w._render.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""App API: Today, Plan, Diary and notes routes (docs/app-api.md).

Google, Todoist, Maps and the rest check are replaced with fakes; diary, goals,
meals, reminders and notes run for real against a temp directory. Nothing here
touches credentials/, memory/ or notes/.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import diary
import goals
import meals
import paths
import reminders
from app_api import routes_diary, routes_plan, routes_today
from app_api.auth import require_device
from app_api.server import ApiError

TZ = ZoneInfo("America/New_York")


def make_client() -> TestClient:
    """Just these three routers, so other areas' work in progress can't break these tests."""
    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status)

    for module in (routes_today, routes_plan, routes_diary):
        app.include_router(module.router)
    app.dependency_overrides[require_device] = lambda: None
    return TestClient(app)


class TempData(unittest.TestCase):
    """Points every store these routes touch at a fresh temp directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "notes").mkdir()
        db = root / "test.db"
        self.notes_dir = root / "notes"
        patches = [
            mock.patch.object(diary, "DB_PATH", db),
            mock.patch.object(goals, "DB_PATH", db),
            mock.patch.object(meals, "DB_PATH", db),
            mock.patch.object(meals, "SETTINGS_PATH", root / "meal_settings.json"),
            mock.patch.object(reminders, "REMINDERS_PATH", root / "reminders.json"),
            mock.patch.object(paths, "NOTES_DIR", self.notes_dir),
            mock.patch.object(paths, "CONFIG_PATH", root / "config.json"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.client = make_client()


def broken(*_a, **_k):
    raise RuntimeError("integration down")


def fake_calendar(events=None, fail=False, create=None):
    cal = SimpleNamespace(
        user_tz=lambda: TZ,
        local_day_start=lambda d: datetime(d.year, d.month, d.day, tzinfo=TZ),
        get_range_events=broken if fail else (lambda a, b: list(events or [])),
        list_calendars=lambda: [{"account": "personal", "id": "cal1", "color": "#9a3f8f"}],
        create_allday_event=create or mock.Mock(return_value={"calendar": "Rest", "id": "x"}),
    )
    return cal


def fake_todoist(tasks=None, projects=None, fail=False):
    td = mock.Mock()
    if fail:
        td.list_projects.side_effect = RuntimeError("todoist down")
        td.list_tasks.side_effect = RuntimeError("todoist down")
    else:
        td.list_projects.return_value = projects or [{"id": "p1", "name": "School"}, {"id": "p2", "name": "Home"}]
        td.list_tasks.return_value = tasks or []
        td._get_paginated.return_value = tasks or []
        td._sort_tasks.side_effect = lambda t: t
    return td


def no_rest():
    return SimpleNamespace(assess=lambda config=None: {"trigger": None})


class TodayTests(TempData):
    def patch_all(self, cal=None, td=None, rest=None, travel=None, signin=None):
        for name, value in (("_calendar", cal or fake_calendar()), ("_todoist", td or fake_todoist()),
                            ("_rest", rest or no_rest()),
                            ("_travel", travel or SimpleNamespace(_api_key=lambda: "")),
                            ("_google_auth", SimpleNamespace(signin_needed=lambda: signin or {}))):
            p = mock.patch.object(routes_today, name, lambda v=value: v)
            p.start()
            self.addCleanup(p.stop)

    def test_shape_and_mapping(self):
        now = datetime.now(timezone.utc)
        today = datetime.now().astimezone().date().isoformat()
        later = now + timedelta(hours=1)
        events = [
            {"id": "e1", "summary": "Lab", "start": later, "end": later + timedelta(hours=1), "is_allday": False,
             "location": "Lab building", "account": "personal", "calendar": "School", "calendar_id": "cal1"},
            {"id": "e2", "summary": "Holiday", "start": datetime.fromisoformat(today + "T00:00:00+00:00"),
             "end": datetime.fromisoformat(today + "T00:00:00+00:00") + timedelta(days=1), "is_allday": True,
             "location": "", "account": "school", "calendar": "Other", "calendar_id": "cal2"},
        ]
        tasks = [{"id": 7, "content": "Essay", "project_id": "p1", "priority": 4, "due": {"date": today}}]
        travel = SimpleNamespace(_api_key=lambda: "key", buffer_seconds=lambda: 300,
                                 estimate=lambda o, d, when=None: {"ok": True, "seconds": 1320, "minutes": 22, "mode": "transit"})
        rest = SimpleNamespace(assess=lambda config=None: {
            "trigger": "packed_stretch", "bad_days": [],
            "packed_stretch": [{"date": "2026-11-18"}, {"date": "2026-11-19"}, {"date": "2026-11-20"}],
            "rest_day": {"date": "2026-11-21", "free_hours": 7.0}})
        self.patch_all(cal=fake_calendar(events), td=fake_todoist(tasks), rest=rest, travel=travel,
                       signin={"school": "the login expired or was revoked"})
        reminders.add_reminder((now + timedelta(minutes=5)).astimezone().isoformat(), "Stretch")
        reminders.add_reminder((now + timedelta(days=3)).isoformat(), "Later")

        r = self.client.get("/v1/today")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["date"], today)
        self.assertEqual(len(body["events"]), 2)
        e1 = body["events"][0]
        self.assertEqual(set(e1), {"id", "account", "calendar", "title", "start", "end", "all_day", "location", "color"})
        self.assertEqual((e1["id"], e1["title"], e1["color"], e1["all_day"]), ("e1", "Lab", "#9A3F8F", False))
        self.assertIsNotNone(datetime.fromisoformat(e1["start"]).tzinfo)
        allday = body["events"][1]
        self.assertTrue(allday["all_day"])
        self.assertIsNone(allday["location"])
        self.assertTrue(allday["start"].startswith(today + "T00:00:00"))

        nxt = body["next"]
        self.assertEqual(nxt["event"]["id"], "e1")
        self.assertEqual((nxt["travel_minutes"], nxt["travel_mode"]), (22, "transit"))
        leave = datetime.fromisoformat(nxt["leave_by"])
        self.assertEqual(datetime.fromisoformat(e1["start"]) - leave, timedelta(seconds=1620))

        self.assertEqual(body["tasks"], [{"id": "7", "title": "Essay", "project": "School", "due": today,
                                          "priority": 1, "completed": False}])
        self.assertEqual([x["text"] for x in body["reminders"]], ["Stretch"])
        self.assertEqual(body["rest"]["day"], "2026-11-21")
        self.assertTrue(body["rest"]["suggested"])
        self.assertIn("Saturday has 7 open hours", body["rest"]["message"])
        self.assertEqual(body["signin_needed"], ["school"])

    def test_no_travel_without_maps_key(self):
        later = datetime.now(timezone.utc) + timedelta(minutes=30)
        events = [{"id": "e1", "summary": "Lab", "start": later, "end": later + timedelta(hours=1),
                   "is_allday": False, "location": "Lab", "account": "a", "calendar": "c", "calendar_id": "x"}]
        estimate = mock.Mock()
        self.patch_all(cal=fake_calendar(events), travel=SimpleNamespace(_api_key=lambda: "", estimate=estimate))
        nxt = self.client.get("/v1/today").json()["next"]
        self.assertEqual(nxt["event"]["id"], "e1")
        self.assertIsNone(nxt["leave_by"])
        self.assertIsNone(nxt["travel_minutes"])
        estimate.assert_not_called()

    def test_travel_error_gives_nulls(self):
        later = datetime.now(timezone.utc) + timedelta(minutes=30)
        events = [{"id": "e1", "summary": "Lab", "start": later, "end": later + timedelta(hours=1),
                   "is_allday": False, "location": "Lab", "account": "a", "calendar": "c", "calendar_id": "x"}]
        self.patch_all(cal=fake_calendar(events), travel=SimpleNamespace(_api_key=lambda: "k", estimate=broken))
        nxt = self.client.get("/v1/today").json()["next"]
        self.assertIsNone(nxt["leave_by"])

    def test_everything_down_still_200(self):
        self.patch_all(cal=fake_calendar(fail=True), td=fake_todoist(fail=True),
                       rest=SimpleNamespace(assess=broken),
                       travel=SimpleNamespace(_api_key=broken))
        with mock.patch.object(reminders, "list_pending", broken):
            r = self.client.get("/v1/today?date=2026-11-16")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body["events"], body["tasks"], body["reminders"]), ([], [], []))
        self.assertIsNone(body["next"])
        self.assertIsNone(body["rest"])

    def test_todoist_not_configured_import_error(self):
        self.patch_all()
        with mock.patch.object(routes_today, "_todoist", broken):
            r = self.client.get("/v1/today")
            self.assertEqual(r.json()["tasks"], [])
            r = self.client.post("/v1/tasks/1/complete")
            self.assertEqual(r.status_code, 503)
            self.assertEqual(r.json()["error"], "todoist_unavailable")

    def test_rest_warnings_off(self):
        rest = SimpleNamespace(assess=mock.Mock())
        self.patch_all(rest=rest)
        paths.CONFIG_PATH.write_text(json.dumps({"rest_day_warnings": False}))
        self.assertIsNone(self.client.get("/v1/today").json()["rest"])
        rest.assess.assert_not_called()

    def test_bad_date(self):
        self.patch_all()
        r = self.client.get("/v1/today?date=tomorrow")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "bad_date")

    def test_complete_and_reopen(self):
        td = fake_todoist()
        self.patch_all(td=td)
        self.assertEqual(self.client.post("/v1/tasks/42/complete").json(), {"ok": True})
        td.complete_task.assert_called_once_with("42")
        self.assertEqual(self.client.post("/v1/tasks/42/reopen").json(), {"ok": True})
        td.reopen_task.assert_called_once_with("42")
        td.complete_task.side_effect = RuntimeError("nope")
        r = self.client.post("/v1/tasks/42/complete")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["error"], "todoist_error")

    def test_plan_rest_day(self):
        create = mock.Mock(return_value={"calendar": "Rest", "id": "x"})
        self.patch_all(cal=fake_calendar(create=create))
        r = self.client.post("/v1/rest/plan", json={"day": "2026-11-19"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])
        self.assertIn("Thursday, November 19", r.json()["message"])
        create.assert_called_once_with("Rest day", "2026-11-19", calendar_name="Rest")

    def test_plan_rest_day_errors(self):
        self.patch_all(cal=fake_calendar(create=mock.Mock(side_effect=RuntimeError("No calendar named 'Rest'"))))
        r = self.client.post("/v1/rest/plan", json={"day": "2026-11-19"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("No calendar named", r.json()["message"])
        self.assertEqual(self.client.post("/v1/rest/plan", json={"day": "Thu"}).status_code, 400)

    def test_rest_json_variants(self):
        self.assertIsNone(routes_today.rest_json({"trigger": None}))
        r = routes_today.rest_json({"trigger": "bad_days", "bad_days": [{"date": "2026-11-14"}, {"date": "2026-11-15"}],
                                    "rest_day": None})
        self.assertTrue(r["suggested"])
        self.assertIsNone(r["day"])
        self.assertIn("2 bad days", r["message"])


class PlanTests(TempData):
    def patch_todoist(self, td):
        p = mock.patch.object(routes_plan, "_todoist", lambda: td)
        p.start()
        self.addCleanup(p.stop)

    def test_plan(self):
        g = goals.add_goal("Pull-ups", category="fitness", target="5 pull-ups")
        goals.add_goal("Read more")
        goals.log_progress(g["id"], "Did some", "3 pull-ups")
        meals.add_meal("Salmon", "meal", ["Greens"], week="this")
        meals.add_meal("Idea", "meal", ["Rice"])            # backlog: not this week
        self.patch_todoist(fake_todoist(tasks=[{"id": 1, "project_id": "p1"}, {"id": 2, "project_id": "p1"}]))

        body = self.client.get("/v1/plan").json()
        first = body["goals"][0]
        self.assertEqual((first["title"], first["target"], first["progress_note"]),
                         ("Pull-ups", "5 pull-ups", "Did some (3 pull-ups)"))
        self.assertIsNotNone(first["last_logged"])
        self.assertIsNone(body["goals"][1]["progress_note"])
        self.assertEqual(body["meals"]["week"], meals.week_start("this"))
        self.assertEqual([m["main"] for m in body["meals"]["items"]], ["Salmon"])
        self.assertEqual(body["meals"]["items"][0]["sides"], ["Greens"])
        self.assertIsNone(body["meals"]["diet"])
        self.assertEqual(body["projects"], [{"id": "p1", "name": "School", "open_count": 2},
                                            {"id": "p2", "name": "Home", "open_count": 0}])

    def test_plan_todoist_down(self):
        self.patch_todoist(fake_todoist(fail=True))
        r = self.client.get("/v1/plan")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["projects"], [])

    def test_goal_progress(self):
        g = goals.add_goal("Run")
        r = self.client.post(f"/v1/goals/{g['id']}/progress", json={"note": "5k", "value": "25 min"})
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(goals.list_goals()[0]["progress"][0]["value"], "25 min")
        self.assertEqual(self.client.post("/v1/goals/999/progress", json={"note": "x"}).status_code, 404)
        self.assertEqual(self.client.post(f"/v1/goals/{g['id']}/progress", json={"note": " "}).status_code, 400)

    def test_meals_add_limit_delete(self):
        ids = []
        for name in ("A", "B", "C"):
            r = self.client.post("/v1/meals", json={"main": name, "kind": "meal", "sides": ["Greens"]})
            self.assertEqual(r.status_code, 200, r.text)
            ids.append(r.json()["id"])
        r = self.client.post("/v1/meals", json={"main": "D", "kind": "meal", "sides": ["Greens"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "meal_rule")
        self.assertIn("4 meals", r.json()["message"])
        self.assertNotIn("Tell the user", r.json()["message"])
        r = self.client.post("/v1/meals", json={"main": "E", "kind": "meal", "sides": ["a", "b", "c"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("1-2 sides", r.json()["message"])
        self.assertEqual(self.client.delete(f"/v1/meals/{ids[0]}").json(), {"ok": True})
        self.assertEqual(self.client.delete(f"/v1/meals/{ids[0]}").status_code, 404)
        self.assertEqual(len(meals.get_week("this")), 2)

    def test_project_tasks(self):
        td = fake_todoist(tasks=[{"id": "t1", "content": "Read ch. 3", "project_id": "p1", "priority": 1}])
        self.patch_todoist(td)
        r = self.client.get("/v1/projects/p1/tasks")
        self.assertEqual(r.json(), [{"id": "t1", "title": "Read ch. 3", "project": "School", "due": None,
                                     "priority": 4, "completed": False}])
        td._get_paginated.assert_called_once_with("/tasks", {"project_id": "p1"})
        td._get_paginated.side_effect = RuntimeError("down")
        self.assertEqual(self.client.get("/v1/projects/p1/tasks").status_code, 502)


class DiaryTests(TempData):
    def test_add_and_list(self):
        for t, text in (("bod", "Plan: lab"), ("eod", "Went fine"), ("bad_day", "Crashed"), ("entry", "Thought")):
            self.assertEqual(self.client.post("/v1/diary", json={"type": t, "text": text}).json(), {"ok": True})
        # Stored exactly as Signal / the tools store them.
        self.assertEqual([e["type"] for e in diary.get_today_entries()], ["bod", "eod", "bad_day", "note"])
        self.assertEqual(diary.get_entries_of_type("bad_day", "2000-01-01")[0]["content"], "Crashed")

        body = self.client.get("/v1/diary?days=14").json()
        self.assertEqual([e["type"] for e in body], ["entry", "bad_day", "eod", "bod"])   # newest first
        self.assertEqual(set(body[0]), {"id", "type", "text", "ts"})
        self.assertIsNotNone(datetime.fromisoformat(body[0]["ts"]).tzinfo)

    def test_rejects_bad_input(self):
        self.assertEqual(self.client.post("/v1/diary", json={"type": "nope", "text": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/v1/diary", json={"type": "bod", "text": "  "}).status_code, 400)

    def test_old_entries_excluded(self):
        diary.store_entry("eod", "old")
        db = diary._get_db()
        db.execute("UPDATE diary SET timestamp = timestamp - 30 * 86400")
        db.commit()
        db.close()
        self.assertEqual(self.client.get("/v1/diary?days=14").json(), [])

    def test_notes(self):
        (self.notes_dir / "groceries.md").write_text("eggs")
        (self.notes_dir / ".hidden").write_text("x")
        (self.notes_dir / "sub").mkdir()
        listed = self.client.get("/v1/notes").json()
        self.assertEqual([n["name"] for n in listed], ["groceries.md"])
        self.assertEqual(self.client.get("/v1/notes/groceries.md").json(), {"name": "groceries.md", "text": "eggs"})
        self.assertEqual(self.client.get("/v1/notes/missing.md").status_code, 404)

    def test_note_traversal(self):
        secret = Path(self.tmp.name) / "secret.txt"
        secret.write_text("top secret")
        for name in ("..%2Fsecret.txt", "%2E%2E%2Fsecret.txt", "..", ".hidden", "..%5Csecret.txt", "sub%2F..%2F..%2Fsecret.txt"):
            r = self.client.get(f"/v1/notes/{name}")
            self.assertIn(r.status_code, (400, 404), name)
            self.assertNotIn("top secret", r.text)
        with self.assertRaises(ApiError):
            routes_diary._note_path("../secret.txt")
        with self.assertRaises(ApiError):
            routes_diary._note_path("/etc/passwd")


if __name__ == "__main__":
    unittest.main()

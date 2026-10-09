"""
Isolated sandbox that runs Savvy's REAL code against a FAKE world.

What is real: the system prompts, chat_with_tools and the whole tool dispatch in
tools.py, calendar_integration.py (all of it — only the Google service object
underneath is fake), todoist_integration.py (only _request is fake), travel.py /
places.py (only the HTTP call to Google Routes is fake), memory.py, diary.py,
reminders.py, task_history.py, core.build_context, signal_bot.process_message
and the scheduler's check-in / reminder / weekly-review functions.

What is fake: Google Calendar + Gmail + Todoist + Routes responses, the clock
(pinned to Monday 2026-10-05 10:00 ET unless a scenario moves it), and the
human at the confirmation prompt (a per-scenario approval policy).

Nothing here touches credentials/, memory/ or notes/ of the real install: paths
are redirected into tests/eval/.sandbox before any app module is imported.

IMPORT ORDER MATTERS: call install() before importing anything from src/.
"""

import copy
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time as _time
import urllib.request
import uuid
from datetime import datetime as _RealDT, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

EVAL_DIR = Path(__file__).resolve().parent
FIXTURES = EVAL_DIR / "fixtures"
PROJECT_ROOT = EVAL_DIR.parent.parent
SRC = PROJECT_ROOT / "src"
SANDBOX = EVAL_DIR / ".sandbox"

TZ = ZoneInfo("America/New_York")
ANCHOR = _RealDT(2026, 10, 5, 10, 0, tzinfo=TZ)

# Config keys copied from the real config so the sandbox behaves like the real
# install (model, account lock, context sizes). Secrets and phone numbers are not.
_SAFE_CONFIG_KEYS = (
    "anthropic_model", "max_tokens", "context_top_k", "recent_window",
    "calendar_account_lock", "default_calendar_account", "default_travel_mode",
    "travel_buffer_minutes", "signal_max_tokens", "checkin_times", "bod_time",
    "eod_time", "weekly_review_day", "weekly_review_time",
)


# =====================================================================
# Clock
# =====================================================================
class Clock:
    """Real time, shifted so that 'now' starts at the scenario's anchor."""

    def __init__(self):
        self.set(ANCHOR)

    def set(self, dt: _RealDT) -> None:
        self._offset = dt.timestamp() - _time.time()

    def time(self) -> float:
        return _time.time() + self._offset

    def now(self) -> _RealDT:
        return _RealDT.fromtimestamp(self.time(), TZ)


CLOCK = Clock()


class ShiftedDateTime(_RealDT):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(CLOCK.time(), tz)

    @classmethod
    def today(cls):
        return cls.fromtimestamp(CLOCK.time())

    @classmethod
    def utcnow(cls):
        return cls.fromtimestamp(CLOCK.time(), timezone.utc).replace(tzinfo=None)


class _TimeShim:
    """Stands in for the `time` module inside memory.py / diary.py."""

    def __getattr__(self, name):
        return getattr(_time, name)

    @staticmethod
    def time():
        return CLOCK.time()


def _parse_dt(value: str) -> _RealDT:
    dt = _RealDT.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


# =====================================================================
# Trace — everything a check can look at
# =====================================================================
class Trace:
    def __init__(self):
        self.turn = 0
        self.calls: list[dict] = []          # every tool call the model made
        self.notifications: list[dict] = []  # scheduler Signal sends
        self.router_calls: list[dict] = []   # paid Routes API lookups
        self.sent_email: list[dict] = []
        self.requests: list[dict] = []       # every Anthropic API request, with usage
        self.pending: dict | None = None     # approval decision of the call in flight

    def tool_names(self, turn: int | None = None) -> list[str]:
        return [c["name"] for c in self.calls if turn is None or c["turn"] == turn]


# =====================================================================
# Fake world state
# =====================================================================
class FakeHttpError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f'<HttpError {status} "{message}">')


class World:
    """Mutable copy of fixtures/world.json for one scenario."""

    def __init__(self, data: dict):
        self.data = data
        self.accounts = dict(data["accounts"])
        self.calendars = copy.deepcopy(data["calendars"])      # account -> [cal]
        self.events: dict[str, dict[str, dict]] = {c["id"]: {} for cals in self.calendars.values() for c in cals}
        self.deleted_events: list[dict] = []
        self.created_event_ids: list[str] = []
        self._load_events()
        self.routes = {k: v for k, v in data["routes"].items()}

        t = data["todoist"]
        self.projects = copy.deepcopy(t["projects"])
        self.labels = [{"id": f"l_{n}", "name": n} for n in t["labels"]]
        self.tasks = {x["id"]: self._api_task(x) for x in t["tasks"]}
        self.completed = [dict(c) for c in t["completed"]]
        self.deleted_tasks: list[dict] = []

        self.emails = {e["id"]: copy.deepcopy(e) for e in data["emails"]}
        self.drafts: dict[str, dict] = {}
        self.gmail_labels = {acct: [{"id": n, "name": n, "type": "system"} for n in
                                    ("INBOX", "UNREAD", "IMPORTANT", "STARRED", "SENT", "TRASH", "DRAFT")]
                             for acct in self.accounts}

    # ---- calendar ----
    def cal_by_name(self, name: str, account: str = "northeastern") -> dict:
        for c in self.calendars[account]:
            if c["summary"].lower() == name.lower():
                return c
        raise KeyError(name)

    def _load_events(self):
        for ev in self.data["events"]:
            acct = ev.get("account", "northeastern")
            cal = self.cal_by_name(ev["calendar"], acct)
            body = {"id": ev["id"], "summary": ev["summary"], "status": "confirmed"}
            if "allday_start" in ev:
                body["start"] = {"date": ev["allday_start"]}
                body["end"] = {"date": ev["allday_end"]}
            else:
                body["start"] = {"dateTime": ev["start"]}
                body["end"] = {"dateTime": ev["end"]}
            if ev.get("location"):
                body["location"] = ev["location"]
            self.events[cal["id"]][ev["id"]] = body

        for rec in self.data.get("recurring_events", []):
            cal = self.cal_by_name(rec["calendar"])
            day = _RealDT.fromisoformat(rec["from"]).date()
            last = _RealDT.fromisoformat(rec["to"]).date()
            sh, sm = map(int, rec["start"].split(":"))
            eh, em = map(int, rec["end"].split(":"))
            series = "rec_" + hashlib.md5(rec["summary"].encode()).hexdigest()[:6]
            while day <= last:
                if day.weekday() in rec["weekdays"]:
                    start = _RealDT(day.year, day.month, day.day, sh, sm, tzinfo=TZ)
                    end = _RealDT(day.year, day.month, day.day, eh, em, tzinfo=TZ)
                    if end <= start:
                        end += timedelta(days=1)
                    eid = f"{series}_{day.strftime('%Y%m%d')}"
                    body = {"id": eid, "summary": rec["summary"], "status": "confirmed",
                            "recurringEventId": series,
                            "start": {"dateTime": start.isoformat()},
                            "end": {"dateTime": end.isoformat()}}
                    if rec.get("location"):
                        body["location"] = rec["location"]
                    self.events[cal["id"]][eid] = body
                day += timedelta(days=1)

    def all_events(self) -> list[dict]:
        """Flat list with calendar name attached, for checks. Recurring series are
        expanded over the fixture's two weeks (instance ids carry the master's prefix)."""
        lo, hi = _RealDT(2026, 10, 1, tzinfo=TZ), _RealDT(2026, 12, 31, tzinfo=TZ)
        out = []
        for acct, cals in self.calendars.items():
            for c in cals:
                for master in self.events.get(c["id"], {}).values():
                    for ev in _expand(master, lo, hi):
                        item = dict(ev)
                        item["_calendar"] = c["summary"]
                        item["_account"] = acct
                        out.append(item)
        return out

    def find_events(self, pattern: str = "", created_only: bool = False) -> list[dict]:
        rx = re.compile(pattern, re.I)
        return [e for e in self.all_events()
                if rx.search(e.get("summary", ""))
                and (not created_only or e["id"] in self.created_event_ids
                     or e.get("recurringEventId") in self.created_event_ids)]

    def created_events(self, pattern: str = "") -> list[dict]:
        return self.find_events(pattern, created_only=True)

    # ---- todoist ----
    @staticmethod
    def _api_task(t: dict) -> dict:
        prio = t.get("priority", "p4")
        return {"id": t["id"], "content": t["content"], "description": t.get("description", ""),
                "project_id": t["project_id"], "priority": 5 - int(prio.lstrip("p")),
                "labels": list(t.get("labels", [])), "parent_id": t.get("parent_id"),
                "checked": False}

    def project_name(self, pid: str) -> str:
        return next((p["name"] for p in self.projects if p["id"] == pid), "")

    def find_tasks(self, pattern: str, include_completed: bool = False) -> list[dict]:
        rx = re.compile(pattern, re.I)
        pool = list(self.tasks.values()) + (self.completed if include_completed else [])
        return [t for t in pool if rx.search(t.get("content", ""))]


def _event_span(ev: dict) -> tuple[_RealDT, _RealDT]:
    """Server-side span. All-day uses the calendar's own zone, as Google does."""
    s, e = ev["start"], ev["end"]
    if "dateTime" in s:
        return _parse_dt(s["dateTime"]), _parse_dt(e["dateTime"])
    start = _RealDT.fromisoformat(s["date"]).replace(tzinfo=TZ)
    end = _RealDT.fromisoformat(e["date"]).replace(tzinfo=TZ)
    return start, end


def _expand(ev: dict, tmin: _RealDT, tmax: _RealDT) -> list[dict]:
    """Instances of a recurring event inside the window, as singleEvents=True returns them.

    Supports what the model is told to use: FREQ=DAILY|WEEKLY, INTERVAL, COUNT,
    UNTIL, and BYDAY for weekly rules. Anything else falls back to the first
    occurrence only.
    """
    rule = next((r[6:] for r in ev.get("recurrence", []) if r.startswith("RRULE:")), None)
    if not rule:
        return [ev]
    parts = dict(p.split("=", 1) for p in rule.split(";") if "=" in p)
    freq = parts.get("FREQ")
    if freq not in ("DAILY", "WEEKLY"):
        return [ev]
    start, end = _event_span(ev)
    start, end = start.astimezone(TZ), end.astimezone(TZ)
    length = end - start
    interval = int(parts.get("INTERVAL", 1))
    count = int(parts["COUNT"]) if "COUNT" in parts else None
    until = None
    if "UNTIL" in parts:
        u = parts["UNTIL"].rstrip("Z")
        until = _RealDT.strptime(u[:8], "%Y%m%d").replace(tzinfo=TZ) + timedelta(days=1)
    days = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
    weekdays = {days[d[-2:]] for d in parts.get("BYDAY", "").split(",") if d[-2:] in days} or {start.weekday()}

    out, n, day = [], 0, start
    horizon = min(tmax.astimezone(TZ), start + timedelta(days=400))
    while day < horizon:
        week_ok = freq == "DAILY" or ((day - start).days // 7) % interval == 0
        day_ok = freq == "DAILY" and (day - start).days % interval == 0 or freq == "WEEKLY" and day.weekday() in weekdays
        if week_ok and day_ok:
            if until and day >= until:
                break
            n += 1
            if count and n > count:
                break
            inst_start = day.replace(hour=start.hour, minute=start.minute)
            if inst_start + length > tmin:
                inst = copy.deepcopy(ev)
                inst.pop("recurrence", None)
                inst["id"] = f"{ev['id']}_{inst_start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"
                inst["recurringEventId"] = ev["id"]
                inst["start"] = {"dateTime": inst_start.isoformat()}
                inst["end"] = {"dateTime": (inst_start + length).isoformat()}
                out.append(inst)
        day += timedelta(days=1)
    return out


def _validate_times(body: dict) -> None:
    for key in ("start", "end"):
        node = body.get(key) or {}
        if "dateTime" in node:
            dt = _RealDT.fromisoformat(str(node["dateTime"]).replace("Z", "+00:00"))
            if dt.tzinfo is None and not node.get("timeZone"):
                raise FakeHttpError(400, f"Missing time zone definition for {key} time.")
        elif "date" not in node:
            raise FakeHttpError(400, f"Missing {key} time.")
    s, e = _event_span(body)
    if e <= s:
        raise FakeHttpError(400, "The specified time range is empty.")


# =====================================================================
# Fake Google Calendar service (what googleapiclient's build() would return)
# =====================================================================
class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeCalendarService:
    def __init__(self, world: World, account: str):
        self.w, self.account = world, account

    def _cal_id(self, cal_id: str) -> str:
        cals = self.w.calendars[self.account]
        if cal_id == "primary":
            return next(c["id"] for c in cals if c.get("primary"))
        if any(c["id"] == cal_id for c in cals):
            return cal_id
        raise FakeHttpError(404, "Not Found")

    # ---- calendarList ----
    def calendarList(self):
        def _list(**_):
            return _Req(lambda: {"items": [
                {"id": c["id"], "summary": c["summary"], "primary": c.get("primary", False),
                 "accessRole": "owner", "backgroundColor": "#9fc6e7"}
                for c in self.w.calendars[self.account]]})
        return SimpleNamespace(list=_list)

    # ---- calendars ----
    def calendars(self):
        def _insert(body, **_):
            def run():
                cid = f"cal_{uuid.uuid4().hex[:8]}@cal.test"
                self.w.calendars[self.account].append({"id": cid, "summary": body["summary"]})
                self.w.events[cid] = {}
                return {"id": cid, "summary": body["summary"]}
            return _Req(run)

        def _delete(calendarId, **_):
            def run():
                cid = self._cal_id(calendarId)
                cals = self.w.calendars[self.account]
                if any(c["id"] == cid and c.get("primary") for c in cals):
                    raise FakeHttpError(400, "Cannot delete primary calendar.")
                self.w.calendars[self.account] = [c for c in cals if c["id"] != cid]
                self.w.events.pop(cid, None)
                return None
            return _Req(run)
        return SimpleNamespace(insert=_insert, delete=_delete)

    # ---- events ----
    def events(self):
        svc = self

        def _list(calendarId, timeMin=None, timeMax=None, maxResults=250, **_):
            def run():
                cid = svc._cal_id(calendarId)
                tmin = _parse_dt(timeMin) if timeMin else _RealDT.min.replace(tzinfo=timezone.utc)
                tmax = _parse_dt(timeMax) if timeMax else _RealDT.max.replace(tzinfo=timezone.utc)
                hits = []
                for master in svc.w.events.get(cid, {}).values():
                    for ev in _expand(master, tmin, tmax):
                        s, e = _event_span(ev)
                        if e > tmin and s < tmax:
                            hits.append((s, copy.deepcopy(ev)))
                hits.sort(key=lambda p: p[0])
                return {"items": [ev for _, ev in hits[:maxResults]]}
            return _Req(run)

        def _get(calendarId, eventId, **_):
            def run():
                cal = svc.w.events.get(svc._cal_id(calendarId), {})
                ev = cal.get(eventId)
                if not ev and "_" in eventId and cal.get(eventId.rsplit("_", 1)[0], {}).get("recurrence"):
                    # An instance of a recurring series: readable, but editing a
                    # single instance isn't simulated.
                    master = cal[eventId.rsplit("_", 1)[0]]
                    ev = next((i for i in _expand(master, _RealDT.min.replace(tzinfo=timezone.utc),
                                                  _RealDT.max.replace(tzinfo=timezone.utc))
                               if i["id"] == eventId), None)
                if not ev:
                    raise FakeHttpError(404, "Not Found")
                return copy.deepcopy(ev)
            return _Req(run)

        def _insert(calendarId, body, **_):
            def run():
                cid = svc._cal_id(calendarId)
                _validate_times(body)
                ev = copy.deepcopy(body)
                ev["id"] = f"evt_{uuid.uuid4().hex[:10]}"
                ev["status"] = "confirmed"
                ev["htmlLink"] = f"https://calendar.test/{ev['id']}"
                svc.w.events[cid][ev["id"]] = ev
                svc.w.created_event_ids.append(ev["id"])
                return copy.deepcopy(ev)
            return _Req(run)

        def _update(calendarId, eventId, body, **_):
            def run():
                cid = svc._cal_id(calendarId)
                if eventId not in svc.w.events.get(cid, {}):
                    raise FakeHttpError(404, "Not Found")
                _validate_times(body)
                ev = copy.deepcopy(body)
                ev["id"] = eventId
                svc.w.events[cid][eventId] = ev
                return copy.deepcopy(ev)
            return _Req(run)

        def _delete(calendarId, eventId, **_):
            def run():
                cid = svc._cal_id(calendarId)
                ev = svc.w.events.get(cid, {}).pop(eventId, None)
                if not ev:
                    raise FakeHttpError(410, "Resource has been deleted")
                svc.w.deleted_events.append(ev)
                return None
            return _Req(run)

        def _quick_add(calendarId, text, **_):
            # Google's NL parser isn't reproducible offline. File it at noon
            # tomorrow so the model sees a (plausibly wrong) parse to react to.
            def run():
                tomorrow = CLOCK.now().replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
                return _insert(calendarId, {
                    "summary": text, "start": {"dateTime": tomorrow.isoformat()},
                    "end": {"dateTime": (tomorrow + timedelta(hours=1)).isoformat()},
                    "description": "[eval] created via quickAdd"}).execute()
            return _Req(run)

        return SimpleNamespace(list=_list, get=_get, insert=_insert, update=_update,
                               delete=_delete, quickAdd=_quick_add)

    # ---- freebusy ----
    def freebusy(self):
        def _query(body, **_):
            def run():
                tmin, tmax = _parse_dt(body["timeMin"]), _parse_dt(body["timeMax"])
                out = {}
                for item in body.get("items", []):
                    cid = self._cal_id(item["id"])
                    busy = []
                    for ev in self.w.events.get(cid, {}).values():
                        s, e = _event_span(ev)
                        if e > tmin and s < tmax:
                            busy.append({"start": s.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                                         "end": e.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")})
                    out[item["id"]] = {"busy": sorted(busy, key=lambda b: b["start"])}
                return {"calendars": out}
            return _Req(run)
        return SimpleNamespace(query=_query)


# =====================================================================
# Fake Todoist HTTP layer (replaces todoist_integration._request)
# =====================================================================
def _make_todoist_request(sb: "Sandbox"):
    import todoist_integration as ti

    def page(items):
        return {"results": copy.deepcopy(items), "next_cursor": None}

    def match_term(t: dict, term: str) -> bool:
        term = term.strip().strip("()").strip()
        neg = term.startswith("!")
        term = term.lstrip("!").strip()
        low = term.lower()
        if low.startswith("search:"):
            hit = low.split(":", 1)[1].strip() in t["content"].lower()
        elif term.startswith("##") or term.startswith("#"):
            hit = sb.world.project_name(t["project_id"]).lower() == term.lstrip("#").strip().lower()
        elif term.startswith("@"):
            hit = term[1:].lower() in [l.lower() for l in t["labels"]]
        elif re.fullmatch(r"p[1-4]", low):
            hit = t["priority"] == 5 - int(low[1])
        elif low in ("no date", "no due date"):
            hit = True
        else:
            raise ti.TodoistError(f"Todoist API 400 on GET /tasks/filter: invalid filter term {term!r}")
        return hit != neg

    def run_filter(query: str) -> list[dict]:
        active = list(sb.world.tasks.values())
        out = []
        for t in active:
            if any(all(match_term(t, a) for a in alt.split("&")) for alt in query.split("|")):
                out.append(t)
        return out

    def request(method: str, path: str, *, params=None, body=None):
        w = sb.world
        if sb.todoist_down:
            raise ti.TodoistError("Could not reach Todoist: [Errno 101] Network is unreachable")
        params = {k: v for k, v in (params or {}).items() if v is not None}
        body = {k: v for k, v in (body or {}).items() if v is not None}

        if method == "GET" and path == "/projects":
            return page(w.projects)
        if method == "POST" and path == "/projects":
            p = {"id": f"p_{uuid.uuid4().hex[:6]}", "name": body["name"], "is_favorite": bool(body.get("is_favorite"))}
            w.projects.append(p)
            return copy.deepcopy(p)
        if method == "GET" and path == "/labels":
            return page(w.labels)
        if method == "GET" and path == "/tasks":
            items = list(w.tasks.values())
            if params.get("project_id"):
                items = [t for t in items if t["project_id"] == params["project_id"]]
            if params.get("label"):
                items = [t for t in items if params["label"] in t["labels"]]
            return page(items)
        if method == "GET" and path == "/tasks/filter":
            return page(run_filter(params.get("query", "")))
        if method == "GET" and path == "/tasks/completed/by_completion_date":
            since, until = _parse_dt(params["since"]), _parse_dt(params["until"])
            items = [c for c in w.completed if since <= _parse_dt(c["completed_at"]) <= until]
            return page(items)
        if method == "POST" and path == "/tasks":
            t = {"id": f"t_{uuid.uuid4().hex[:8]}", "content": body["content"],
                 "description": body.get("description", ""),
                 "project_id": body.get("project_id") or "p_inbox",
                 "priority": body.get("priority", 1), "labels": body.get("labels", []),
                 "parent_id": body.get("parent_id"), "checked": False}
            w.tasks[t["id"]] = t
            return copy.deepcopy(t)

        m = re.fullmatch(r"/tasks/([^/]+)(/close|/reopen)?", path)
        if m:
            tid, action = m.group(1), m.group(2)
            if action == "/reopen":
                done = next((c for c in w.completed if c["id"] == tid), None)
                if not done:
                    raise ti.TodoistError(f"Not found: {method} {path} — the id may be wrong or already deleted.")
                w.completed.remove(done)
                w.tasks[tid] = {k: v for k, v in done.items() if k != "completed_at"}
                return None
            task = w.tasks.get(tid)
            if not task:
                raise ti.TodoistError(f"Not found: {method} {path} — the id may be wrong or already deleted.")
            if method == "GET" and not action:
                return copy.deepcopy(task)
            if method == "POST" and action == "/close":
                del w.tasks[tid]
                w.completed.append(dict(task, completed_at=CLOCK.now().astimezone(timezone.utc).isoformat()))
                return None
            if method == "POST" and not action:
                for f in ("content", "description", "priority", "labels", "project_id"):
                    if f in body:
                        task[f] = body[f]
                return copy.deepcopy(task)
            if method == "DELETE":
                w.deleted_tasks.append(w.tasks.pop(tid))
                return None
        raise ti.TodoistError(f"Todoist API 404 on {method} {path}: (not simulated)")

    return request


# =====================================================================
# Fake Gmail (function level — email_integration's formatters stay real)
# =====================================================================
def _install_fake_email(sb: "Sandbox"):
    import email_integration as ei

    def _summary(e):
        return {"id": e["id"], "thread_id": e["thread_id"], "from": e["from"], "subject": e["subject"],
                "date": e["date"], "snippet": e["body"][:150], "labels": list(e["labels"]),
                "is_unread": "UNREAD" in e["labels"], "account": e["account"]}

    def _inbox():
        return sorted((e for e in sb.world.emails.values() if "TRASH" not in e["labels"]),
                      key=lambda e: e["date"], reverse=True)

    def get_recent_emails(max_results=10, hours_back=24):
        cutoff = CLOCK.now() - timedelta(hours=hours_back)
        return [_summary(e) for e in _inbox() if _parse_dt(e["date"]) >= cutoff][:max_results]

    def get_unread_count():
        counts = {a: 0 for a in sb.world.accounts}
        for e in _inbox():
            if "UNREAD" in e["labels"]:
                counts[e["account"]] += 1
        return counts

    def get_important_unread(max_results=5):
        return [_summary(e) for e in _inbox()
                if "UNREAD" in e["labels"] and "IMPORTANT" in e["labels"]][:max_results]

    def search_emails(query, max_results=5):
        terms = query.split()
        out = []
        for e in _inbox():
            hay = f"{e['from']} {e['subject']} {e['body']}".lower()
            ok = True
            for term in terms:
                t = term.lower()
                if t.startswith("from:"):
                    ok &= t[5:] in e["from"].lower()
                elif t.startswith("subject:"):
                    ok &= t[8:] in e["subject"].lower()
                elif t == "is:unread":
                    ok &= "UNREAD" in e["labels"]
                elif t == "is:important":
                    ok &= "IMPORTANT" in e["labels"]
                elif t.startswith(("in:", "newer_than:", "after:", "before:", "label:")):
                    continue
                else:
                    ok &= t.strip('"') in hay
            if ok:
                out.append(_summary(e))
        return out[:max_results]

    def read_full_email(message_id, account=None):
        e = sb.world.emails.get(message_id)
        if not e:
            return None
        return {"from": e["from"], "to": e["to"], "cc": "", "date": e["date"],
                "subject": e["subject"], "body": e["body"]}

    def read_thread(thread_id, account=None):
        msgs = [e for e in sb.world.emails.values() if e["thread_id"] == thread_id]
        if not msgs:
            return None
        return {"account": msgs[0]["account"],
                "messages": [{"from": m["from"], "date": m["date"], "body": m["body"]} for m in msgs]}

    def _relabel(message_id, add=(), remove=()):
        e = sb.world.emails.get(message_id)
        if not e:
            return False
        e["labels"] = [l for l in e["labels"] if l not in remove] + [l for l in add if l not in e["labels"]]
        return True

    def send_email(to, subject, body, account=None, cc="", bcc="", reply_to_id=None):
        rec = {"to": to, "subject": subject, "body": body, "account": account, "cc": cc,
               "reply_to_id": reply_to_id}
        sb.trace.sent_email.append(rec)
        return {"id": f"sent_{uuid.uuid4().hex[:6]}"}

    def draft_email(to, subject, body, account=None):
        did = f"draft_{uuid.uuid4().hex[:6]}"
        sb.world.drafts[did] = {"id": did, "to": to, "subject": subject, "body": body, "account": account}
        return {"id": did}

    def list_drafts(max_results=10, account=None):
        return list(sb.world.drafts.values())[:max_results]

    def send_draft(draft_id, account=None):
        d = sb.world.drafts.pop(draft_id, None)
        if not d:
            return None
        sb.trace.sent_email.append(dict(d, via="draft"))
        return {"id": draft_id}

    def delete_draft(draft_id, account=None):
        return sb.world.drafts.pop(draft_id, None) is not None

    def list_labels(account=None):
        acct = account or "northeastern"
        return list(sb.world.gmail_labels.get(acct, []))

    def create_label(name, account=None):
        lbl = {"id": f"Label_{uuid.uuid4().hex[:4]}", "name": name, "type": "user"}
        sb.world.gmail_labels.setdefault(account or "northeastern", []).append(lbl)
        return lbl

    fakes = dict(
        get_recent_emails=get_recent_emails, get_unread_count=get_unread_count,
        get_important_unread=get_important_unread, search_emails=search_emails,
        read_full_email=read_full_email, read_thread=read_thread,
        send_email=send_email, draft_email=draft_email, list_drafts=list_drafts,
        send_draft=send_draft, delete_draft=delete_draft, list_labels=list_labels,
        create_label=create_label,
        modify_email=lambda message_id, add_labels=None, remove_labels=None, account=None:
            _relabel(message_id, add_labels or (), remove_labels or ()),
        star_email=lambda message_id, account=None: _relabel(message_id, add=["STARRED"]),
        mark_read=lambda message_id, account=None: _relabel(message_id, remove=["UNREAD"]),
        mark_unread=lambda message_id, account=None: _relabel(message_id, add=["UNREAD"]),
        archive_email=lambda message_id, account=None: _relabel(message_id, remove=["INBOX"]),
        trash_email=lambda message_id, account=None: _relabel(message_id, add=["TRASH"], remove=["INBOX"]),
    )
    for name, fn in fakes.items():
        setattr(ei, name, fn)


# =====================================================================
# Embeddings
# =====================================================================
_STOP = set("a an the and or of to in on at for is are was were be been it its this that with by as from "
            "user user's their they them i me my you your".split())


def _embed_bow(text: str, dims: int = 512) -> list[float]:
    """Deterministic hashed bag-of-words. Used only when Ollama is unreachable;
    semantic retrieval gets noticeably worse, so memory results are less meaningful."""
    vec = [0.0] * dims
    for tok in re.findall(r"[a-z0-9']+", (text or "").lower()):
        if tok in _STOP:
            continue
        tok = tok.rstrip("s") if len(tok) > 4 else tok
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % dims] += 1.0 if (h >> 12) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec] if any(vec) else []


def _ollama_up() -> bool:
    """Probe a real embed call: /api/tags can answer while /api/embed 500s
    (a stale Ollama process after an upgrade), which silently turns semantic
    retrieval into recency-only."""
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/embed",
            data=json.dumps({"model": "nomic-embed-text", "input": "probe"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return bool(json.load(r).get("embeddings"))
    except Exception:
        return False


# =====================================================================
# Sandbox
# =====================================================================
class Sandbox:
    def __init__(self):
        self.trace = Trace()
        self.world: World | None = None
        self.approval_policy = lambda name, inp: True
        self.todoist_down = False
        self.embed_mode = "?"
        self.config: dict = {}
        self.client = None
        self.model = ""
        self.m = SimpleNamespace()  # imported app modules

    # ------------------------------------------------------------------
    def install(self, model: str | None = None, embed: str = "auto", overrides: dict | None = None) -> "Sandbox":
        os.environ["TZ"] = "America/New_York"
        _time.tzset()
        sys.path.insert(0, str(SRC))

        (SANDBOX / "credentials").mkdir(parents=True, exist_ok=True)
        (SANDBOX / "memory").mkdir(parents=True, exist_ok=True)
        (SANDBOX / "notes").mkdir(parents=True, exist_ok=True)

        # 1. Config: safe keys from the real install + sandbox overrides.
        real_cfg = {}
        try:
            real_cfg = json.loads((PROJECT_ROOT / "credentials" / "config.json").read_text())
        except (OSError, json.JSONDecodeError):
            pass
        cfg = {k: real_cfg[k] for k in _SAFE_CONFIG_KEYS if k in real_cfg}
        cfg.setdefault("anthropic_model", "claude-sonnet-4-6")
        if model:
            cfg["anthropic_model"] = model
        cfg.update({
            "sender_number": "+15550000000", "recipient_number": "+15550000001",
            "quiet_hours_start": 23, "quiet_hours_end": 6, "max_notifications_per_day": 100,
            "liveness_alerts": False, "google_maps_api_key": "eval-fake-key",
        })
        cfg.update(overrides or {})
        self.config = cfg
        (SANDBOX / "credentials" / "config.json").write_text(json.dumps(cfg, indent=2))

        # Danger levels are the real ones — confirmation gating is part of what's tested.
        try:
            levels = json.loads((PROJECT_ROOT / "credentials" / "tool_config.json").read_text())
            (SANDBOX / "credentials" / "tool_config.json").write_text(json.dumps(
                {"danger_levels": levels.get("danger_levels", {}), "session_allowlist": []}, indent=2))
        except (OSError, json.JSONDecodeError):
            print("[sandbox] WARNING: no credentials/tool_config.json — every tool defaults to level 2")

        if not os.environ.get("ANTHROPIC_API_KEY") and real_cfg.get("anthropic_api_key"):
            os.environ["ANTHROPIC_API_KEY"] = real_cfg["anthropic_api_key"]   # process-only
        os.environ["TODOIST_API_TOKEN"] = "eval-fake-token"
        os.environ["GOOGLE_MAPS_API_KEY"] = "eval-fake-key"

        # 2. Redirect every path BEFORE any other app module binds them.
        import paths
        paths.CREDENTIALS_DIR = SANDBOX / "credentials"
        paths.CONFIG_PATH = paths.CREDENTIALS_DIR / "config.json"
        paths.MEMORY_DIR = SANDBOX / "memory"
        paths.DB_PATH = paths.MEMORY_DIR / "secretary_memory.db"
        paths.SCHEDULER_STATE_PATH = paths.MEMORY_DIR / "scheduler_state.json"
        paths.NOTES_DIR = SANDBOX / "notes"
        paths.SAVVY_RULES_PATH = paths.NOTES_DIR / "savvy_rules.md"

        # 3. Memory + embeddings.
        import memory
        memory.time = _TimeShim()
        if embed == "ollama" or (embed == "auto" and _ollama_up()):
            self.embed_mode = "ollama"
        else:
            if embed == "ollama":
                raise SystemExit("Ollama /api/embed with nomic-embed-text is not working.")
            if embed == "auto":
                print("[sandbox] Ollama /api/embed isn't working — falling back to bag-of-words "
                      "embeddings; memory-retrieval results will be less realistic.")
            memory._embed = _embed_bow
            self.embed_mode = "bow"

        # 4. Calendar: real module, fake Google service underneath.
        import calendar_integration as ci
        ci._get_services = lambda: [(label, FakeCalendarService(self.world, label))
                                    for label in self.world.accounts]
        ci.get_account_labels = lambda: sorted(self.world.accounts)
        ci.stored_account_email = lambda label: self.world.accounts.get(label, "")

        # 5. Todoist: real module, fake HTTP.
        import todoist_integration as ti
        ti._request = _make_todoist_request(self)

        # 6. Gmail: fake functions (must precede tools/core, which import by name).
        _install_fake_email(self)

        # 7. Travel: real estimate/cache/override logic, fake Routes call.
        import places, travel

        def fake_routes(a, b, mode, when):
            key = "|".join(sorted([a["key"], b["key"]])) + f"|{mode}"
            alt = f"{a['key']}|{b['key']}|{mode}", f"{b['key']}|{a['key']}|{mode}"
            minutes = next((self.world.routes[k] for k in (key, *alt) if k in self.world.routes), None)
            self.trace.router_calls.append({"from": a["key"], "to": b["key"], "mode": mode,
                                            "minutes": minutes})
            return None if minutes is None else int(minutes) * 60
        travel._routes_duration = fake_routes
        travel._api_key = lambda: "eval-fake-key"

        # 8. Tools: approval policy + call recording.
        import logging
        import tools, tool_diagnostics
        tool_diagnostics.AUDIT_LOG = paths.MEMORY_DIR / "tool_audit.jsonl"
        tool_diagnostics.print = lambda *a, **k: None      # the trace records everything it prints
        tools.print = lambda *a, **k: None
        logging.getLogger("httpx").setLevel(logging.WARNING)

        def review(name, inp):
            level = tool_diagnostics.get_danger_level(name)
            prompted = level >= 2 or (level == 1 and inp.get("_importance") == "high")
            approved = bool(self.approval_policy(name, inp)) if prompted else True
            self.trace.pending = {"level": level, "prompted": prompted, "approved": approved}
            return uuid.uuid4().hex[:8], approved
        tools.review_tool_call = review

        original_execute = tools.execute_tool

        def recording_execute(name, inp):
            self.trace.pending = None
            result = original_execute(name, inp)
            decision = self.trace.pending or {"level": None, "prompted": False, "approved": True}
            self.trace.calls.append({"turn": self.trace.turn, "name": name, "input": dict(inp),
                                     "result": result, **decision})
            return result
        tools.execute_tool = recording_execute

        # tools._json_default checks isinstance(o, datetime); once `datetime` is
        # swapped for ShiftedDateTime, plain datetimes would fail that check.
        def json_default(o):
            if isinstance(o, _RealDT):
                return o.isoformat()
            raise TypeError(f"Not serializable: {type(o).__name__}")
        tools._json_default = json_default

        # 9. Everything else.
        import core, prompt, diary, reminders, task_history, scheduler, signal_bot, email_integration  # noqa: E401
        import goals, meals, rest
        diary.time = _TimeShim()
        goals.time = _TimeShim()
        meals.time = _TimeShim()
        for mod in (core, tools, ci, diary, reminders, task_history, travel, ti, scheduler,
                    signal_bot, email_integration, memory, goals, meals, rest):
            if hasattr(mod, "datetime"):
                mod.datetime = ShiftedDateTime

        def capture_notification(title, body, config=None):
            self.trace.notifications.append({"title": title, "body": body,
                                             "at": CLOCK.now().isoformat()})
            return True
        scheduler.send_notification = capture_notification
        scheduler.MODEL = cfg["anthropic_model"]

        self.m = SimpleNamespace(core=core, tools=tools, prompt=prompt, memory=memory, diary=diary,
                                 reminders=reminders, task_history=task_history, scheduler=scheduler,
                                 signal_bot=signal_bot, places=places, travel=travel, paths=paths,
                                 calendar=ci, todoist=ti, goals=goals, meals=meals, rest=rest)
        self.model = cfg["anthropic_model"]
        return self

    # ------------------------------------------------------------------
    def reset(self, now: _RealDT | None = None, approve=None) -> None:
        """Fresh world, fresh memory, clock at `now` (default: the anchor)."""
        CLOCK.set(now or ANCHOR)
        self.trace = Trace()
        self.approval_policy = approve or (lambda name, inp: True)
        self.todoist_down = False

        for sub in ("memory", "notes"):
            shutil.rmtree(SANDBOX / sub, ignore_errors=True)
            (SANDBOX / sub).mkdir(parents=True)

        self.world = World(json.loads((FIXTURES / "world.json").read_text()))
        mem_fx = json.loads((FIXTURES / "memory.json").read_text())

        paths = self.m.paths
        (paths.NOTES_DIR / "places.json").write_text(json.dumps(self.world.data["places"], indent=2))
        shutil.copy(FIXTURES / "savvy_rules.md", paths.SAVVY_RULES_PATH)
        (paths.MEMORY_DIR / "scheduled_reminders.json").write_text(json.dumps(mem_fx["reminders"], indent=2))

        self._seed_history(mem_fx["task_history"])
        self._seed_db(mem_fx)
        self._seed_goals_and_meals(mem_fx)

    def _seed_history(self, records: list[dict]) -> None:
        th = self.m.task_history
        th.HISTORY_DIR = self.m.paths.MEMORY_DIR / "task_history"
        th.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        for rec in records:
            ts = _RealDT.fromtimestamp(CLOCK.time() - rec["days_ago"] * 86400, timezone.utc)
            line = {"timestamp": ts.isoformat(), "tool": rec["tool"], "input": rec["input"],
                    "result": rec["result"]}
            with open(th._week_file(ts), "a") as f:
                f.write(json.dumps(line) + "\n")

    def _seed_db(self, fx: dict) -> None:
        mem = self.m.memory
        now = CLOCK.time()
        db = mem._get_db()
        for f in fx["facts"]:
            emb = mem._embed(f["fact"])
            db.execute("INSERT INTO facts (fact, embedding, source_conversation_id, timestamp, fact_type) "
                       "VALUES (?, ?, NULL, ?, ?)",
                       (f["fact"], json.dumps(emb) if emb else None, now - f["age_days"] * 86400, f["type"]))
        for c in fx["conversations"]:
            emb = mem._embed(c["content"])
            db.execute("INSERT INTO conversations (role, content, embedding, timestamp, session_id) "
                       "VALUES (?, ?, ?, ?, 'signal_bot')",
                       (c["role"], c["content"], json.dumps(emb) if emb else None, now - c["days_ago"] * 86400))
        db.commit()
        db.close()

        ddb = self.m.diary._get_db()
        for d in fx["diary"]:
            ddb.execute("INSERT INTO diary (entry_type, content, llm_response, date, timestamp) "
                        "VALUES (?, ?, '', ?, ?)",
                        (d["entry_type"], d["content"], d["date"], now - d["days_ago"] * 86400))
        ddb.commit()
        ddb.close()

    def _seed_goals_and_meals(self, fx: dict) -> None:
        g, m = self.m.goals, self.m.meals
        self.goal_ids = {}
        for item in fx.get("goals", []):
            row = g.add_goal(item["title"], item.get("category", ""), item.get("target", ""), item.get("why", ""),
                             item.get("due_date"), self.goal_ids.get(item.get("parent")))
            self.goal_ids[item["key"]] = row["id"]
            for p in item.get("progress", []):
                g.log_progress(row["id"], p["note"], p.get("value", ""))
                db = g._db()
                db.execute("UPDATE goal_progress SET timestamp = ? WHERE goal_id = ? AND note = ?",
                           (CLOCK.time() - p["days_ago"] * 86400, row["id"], p["note"]))
                db.commit()
                db.close()
        for item in fx.get("meals", []):
            m.add_meal(item["main"], item["kind"], item.get("sides"), item["week"], item.get("day"),
                       allow_over_limit=True)

    def goals(self, status: str = "all") -> list[dict]:
        return self.m.goals.list_goals(status)

    def meals(self, week) -> list[dict]:
        return self.m.meals.get_week(week)

    def rest_check(self) -> str:
        self.trace.turn += 1
        mem = self.m.memory.Memory(session_id="scheduler")
        try:
            self.m.scheduler.rest_day_check(self.client_(), mem, {}, self.config)
        finally:
            mem.close()
        sent = [n["body"] for n in self.trace.notifications if n["title"] == "Rest day"]
        return sent[-1] if sent else "(no rest-day message sent)"

    def add_event(self, summary: str, start, end, calendar: str = "Research") -> None:
        """Put an extra event on the fake calendar (scenario setup)."""
        cal = self.world.cal_by_name(calendar)
        eid = f"evt_setup_{len(self.world.events[cal['id']])}"
        self.world.events[cal["id"]][eid] = {"id": eid, "summary": summary, "status": "confirmed",
                                             "start": {"dateTime": start.isoformat()},
                                             "end": {"dateTime": end.isoformat()}}

    def meal_reminder(self) -> str:
        self.trace.turn += 1
        mem = self.m.memory.Memory(session_id="scheduler")
        try:
            self.m.scheduler.meal_reminder(mem, {}, self.config)
        finally:
            mem.close()
        sent = [n["body"] for n in self.trace.notifications if n["title"] == "Meal plan"]
        return sent[-1] if sent else "(no meal reminder sent)"

    # ------------------------------------------------------------------
    # Read-back helpers for checks
    # ------------------------------------------------------------------
    def facts(self) -> list[dict]:
        db = self.m.memory._get_db()
        rows = db.execute("SELECT fact, fact_type, timestamp FROM facts").fetchall()
        db.close()
        return [{"fact": f, "type": t, "age_days": (CLOCK.time() - ts) / 86400} for f, t, ts in rows]

    def rules(self) -> str:
        return self.m.paths.load_savvy_rules()

    def reminders(self) -> list[dict]:
        return self.m.reminders.list_pending()

    def diary_today(self) -> list[dict]:
        return self.m.diary.get_today_entries()

    def places(self) -> dict:
        return self.m.places.load()

    # ------------------------------------------------------------------
    # Drivers — one per surface
    # ------------------------------------------------------------------
    def client_(self):
        """Anthropic client whose messages.create records every request's usage."""
        if self.client is None:
            import anthropic
            self.client = anthropic.Anthropic()
            real_create = self.client.messages.create

            def recording_create(*args, **kwargs):
                resp = real_create(*args, **kwargs)
                u = resp.usage
                self.trace.requests.append({
                    "turn": self.trace.turn, "model": kwargs.get("model"),
                    "tools_sent": len(kwargs.get("tools") or []),
                    "purpose": "tool loop" if kwargs.get("tools") else "fact extraction/other",
                    "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                    "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                    "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
                    "stop_reason": resp.stop_reason,
                })
                return resp
            self.client.messages.create = recording_create
        return self.client

    def signal_turn(self, text: str) -> str:
        self.trace.turn += 1
        mem = self.m.memory.Memory(session_id="signal_bot")
        try:
            return self.m.signal_bot.process_message(self.client_(), mem, text, self.config)
        finally:
            mem.close()

    def repl_turn(self, text: str) -> str:
        """Exactly what the REPL loop does: core.respond on the repl surface."""
        self.trace.turn += 1
        mem = self.m.memory.Memory(session_id="eval_repl")
        try:
            reply, _, _ = self.m.core.respond(self.client_(), mem, text, surface="repl", config=self.config)
            return reply
        finally:
            mem.close()

    def checkin(self, label: str) -> str:
        self.trace.turn += 1
        sch = self.m.scheduler
        mem = self.m.memory.Memory(session_id="scheduler")
        try:
            sch.smart_checkin(self.client_(), mem, {}, self.config, label)
        finally:
            mem.close()
        sent = [n for n in self.trace.notifications if n["title"] == "Check-in"]
        return sent[-1]["body"] if sent else "NONE (no notification sent)"

    def run_reminders(self) -> str:
        self.trace.turn += 1
        sch = self.m.scheduler
        mem = self.m.memory.Memory(session_id="scheduler")
        try:
            sch.run_due_reminders(self.client_(), mem, {}, self.config)
        finally:
            mem.close()
        sent = [n["body"] for n in self.trace.notifications if n["title"] == "Scheduled Check-in"]
        return "\n---\n".join(sent) if sent else "(no reminder fired)"

    def weekly_review(self) -> str:
        self.trace.turn += 1
        sch = self.m.scheduler
        mem = self.m.memory.Memory(session_id="scheduler")
        try:
            sch.weekly_review(self.client_(), mem, {}, self.config)
        finally:
            mem.close()
        sent = [n["body"] for n in self.trace.notifications if n["title"] == "Weekly Review"]
        return sent[-1] if sent else "(weekly review did not run)"

    # ------------------------------------------------------------------
    def preview_context(self, surface: str, text: str = "what's up?") -> str:
        """The exact system prompt the model would get — no API call. For --dry-run."""
        mem = self.m.memory.Memory(session_id="preview")
        try:
            system, messages = self.m.core.build_context(text, mem, self.m.prompt.SYSTEM_PROMPT, surface)
            parts = [f"[system block {i + 1}{' — CACHED' if b.get('cache_control') else ''}]\n{b['text']}"
                     for i, b in enumerate(system)]
            return "\n\n".join(parts) + "\n\n[messages]\n" + json.dumps(messages, indent=1)[:3000]
        finally:
            mem.close()


SB = Sandbox()


def at(day: int, hour: int, minute: int = 0) -> _RealDT:
    """A clock time in October 2026, ET. at(8, 13) == Thu Oct 8, 1:00 PM."""
    return _RealDT(2026, 10, day, hour, minute, tzinfo=TZ)

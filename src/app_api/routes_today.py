"""Today, task complete/reopen, rest-day plan. OWNER: today-plan-diary agent.

Every integration here can be missing or failing (no Google login, no Todoist
token, no Maps key, network down). Today degrades each part to empty or null
instead of failing the whole screen.
"""

import json
import logging
from datetime import date as _date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app_api.auth import require_device
from app_api.server import ApiError

router = APIRouter(prefix="/v1", dependencies=[Depends(require_device)])
log = logging.getLogger("savvy.app_api.today")


# ---------------------------------------------------------------------------
# Integration access. Imported lazily: todoist_integration raises at import
# when no token is configured, and tests replace these with fakes.
# ---------------------------------------------------------------------------
def _calendar():
    import calendar_integration
    return calendar_integration


def _todoist():
    import todoist_integration
    return todoist_integration


def _travel():
    import travel
    return travel


def _rest():
    import rest
    return rest


def _reminders():
    import reminders
    return reminders


def _google_auth():
    import google_auth
    return google_auth


def _config() -> dict:
    from paths import CONFIG_PATH
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------
def _hex_color(value) -> str | None:
    v = (value or "").strip()
    if len(v) == 7 and v.startswith("#"):
        try:
            int(v[1:], 16)
            return v.upper()
        except ValueError:
            return None
    return None


def event_json(e: dict, cal, colors: dict) -> dict:
    """A calendar_integration event dict → the contract's Event."""
    if e.get("is_allday"):
        # All-day events come back as UTC midnight of the date: report the
        # user's local midnight of that same date instead.
        start = cal.local_day_start(e["start"].date())
        end = cal.local_day_start(e["end"].date())
    else:
        tz = cal.user_tz()
        start, end = e["start"].astimezone(tz), e["end"].astimezone(tz)
    return {
        "id": str(e.get("id") or ""),
        "account": e.get("account") or "",
        "calendar": e.get("calendar") or "",
        "title": e.get("summary") or "(no title)",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "all_day": bool(e.get("is_allday")),
        "location": (e.get("location") or "").strip() or None,
        "color": colors.get((e.get("account"), e.get("calendar_id"))),
    }


def task_json(t: dict, project_names: dict[str, str] | None = None) -> dict:
    """A Todoist API task → the contract's Task.

    priority is Todoist's app numbering: 1 = urgent ... 4 = normal (the API
    counts the other way; todoist_integration._to_ui_priority converts).
    """
    due = t.get("due") or {}
    due_date = (due.get("date") or "")[:10] if isinstance(due, dict) else ""
    try:
        priority = 5 - int(t.get("priority") or 1)
    except (TypeError, ValueError):
        priority = 4
    pid = str(t.get("project_id") or "")
    return {
        "id": str(t.get("id")),
        "title": t.get("content") or "",
        "project": (project_names or {}).get(pid) or None,
        "due": due_date or None,
        "priority": min(4, max(1, priority)),
        "completed": bool(t.get("checked") or t.get("is_completed") or t.get("completed_at")),
    }


def _calendar_colors(cal) -> dict:
    try:
        return {(c.get("account"), c.get("id")): _hex_color(c.get("color")) for c in cal.list_calendars()}
    except Exception as e:
        log.warning("calendar colors unavailable: %s", e)
        return {}


def _events(day: str) -> list[dict]:
    try:
        cal = _calendar()
        raw = cal.get_range_events(day, day)
        colors = _calendar_colors(cal) if raw else {}
        return [event_json(e, cal, colors) for e in raw]
    except Exception as e:
        log.warning("calendar unavailable: %s", e)
        return []


def _next(events: list[dict], day: str) -> dict | None:
    """The next timed event today, with leave-by time when travel can say."""
    now = datetime.now(timezone.utc)
    if day != datetime.now().astimezone().date().isoformat():
        return None
    upcoming = [e for e in events if not e["all_day"] and datetime.fromisoformat(e["start"]) > now]
    if not upcoming:
        return None
    event = upcoming[0]
    out = {"event": event, "leave_by": None, "travel_minutes": None, "travel_mode": None}
    if not event["location"]:
        return out
    try:
        travel = _travel()
        if not travel._api_key():
            return out
        start = datetime.fromisoformat(event["start"])
        est = travel.estimate("home", event["location"], when=start)
        if est.get("ok") and est.get("seconds") is not None:
            leave = start - timedelta(seconds=est["seconds"] + travel.buffer_seconds())
            out.update(leave_by=leave.isoformat(), travel_minutes=est["minutes"], travel_mode=est["mode"])
    except Exception as e:
        log.warning("travel estimate failed: %s", e)
    return out


def _tasks() -> list[dict]:
    try:
        td = _todoist()
        names = {str(p["id"]): p.get("name", "") for p in td.list_projects()}
        # Todoist evaluates the filter server-side; most tasks are undated by
        # design, so this is usually a short list.
        return [task_json(t, names) for t in td.list_tasks(filter_query="today | overdue", max_results=100)]
    except Exception as e:
        log.warning("todoist unavailable: %s", e)
        return []


def _reminders_for(day: str) -> list[dict]:
    try:
        out = []
        for r in _reminders().list_pending():
            try:
                at = datetime.fromisoformat(r["due_at"])
                local = at.astimezone() if at.tzinfo else at
            except (KeyError, TypeError, ValueError):
                continue
            if local.date().isoformat() == day:
                out.append({"id": str(r.get("id")), "text": r.get("instruction") or "", "at": local.isoformat()})
        return out
    except Exception as e:
        log.warning("reminders unavailable: %s", e)
        return []


def _fmt_day(iso: str) -> str:
    d = _date.fromisoformat(iso)
    return f"{d:%A}"


def rest_json(a: dict) -> dict | None:
    """rest.assess() → {suggested, day, message}, or None when there's no warning."""
    if not a or not a.get("trigger"):
        return None
    rd = a.get("rest_day")
    if a["trigger"] == "bad_days":
        n = len({b["date"] for b in a.get("bad_days") or []})
        why = f"You've logged {n} bad days recently."
    else:
        stretch = a.get("packed_stretch") or []
        first = _fmt_day(stretch[0]["date"]) if stretch else "soon"
        why = f"{len(stretch)} packed days in a row from {first}."
    if rd:
        hours = rd.get("free_hours")
        hours_txt = f"{hours:g}" if isinstance(hours, (int, float)) else str(hours)
        msg = f"{why} {_fmt_day(rd['date'])} has {hours_txt} open hours: keep it a stay-home day?"
        return {"suggested": True, "day": rd["date"], "message": msg}
    return {"suggested": True, "day": None,
            "message": f"{why} No day in the next few has enough open time; maybe lighten one instead."}


def _rest_suggestion(config: dict) -> dict | None:
    if config.get("rest_day_warnings") is False:
        return None
    try:
        return rest_json(_rest().assess(config=config))
    except Exception as e:
        log.warning("rest check failed: %s", e)
        return None


def _signin_needed() -> list[str]:
    try:
        return sorted(_google_auth().signin_needed())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/today")
def today(date: str | None = Query(default=None)):
    if date:
        try:
            _date.fromisoformat(date)
        except ValueError:
            raise ApiError(400, "bad_date", "date must be YYYY-MM-DD.")
    day = date or datetime.now().astimezone().date().isoformat()
    events = _events(day)
    config = _config()
    return {
        "date": day,
        "next": _next(events, day),
        "events": events,
        "tasks": _tasks(),
        "reminders": _reminders_for(day),
        "rest": _rest_suggestion(config),
        # After the calendar fetch, which is what notices a dead login.
        "signin_needed": _signin_needed(),
    }


def _todoist_or_503():
    try:
        return _todoist()
    except Exception as e:
        raise ApiError(503, "todoist_unavailable", f"Todoist isn't set up: {e}")


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: str):
    td = _todoist_or_503()
    try:
        td.complete_task(task_id)
    except Exception as e:
        raise ApiError(502, "todoist_error", f"Todoist didn't complete the task: {e}")
    return {"ok": True}


@router.post("/tasks/{task_id}/reopen")
def reopen_task(task_id: str):
    td = _todoist_or_503()
    try:
        td.reopen_task(task_id)
    except Exception as e:
        raise ApiError(502, "todoist_error", f"Todoist didn't reopen the task: {e}")
    return {"ok": True}


class RestPlan(BaseModel):
    day: str


@router.post("/rest/plan")
def plan_rest_day(body: RestPlan):
    """Block the day as an all-day "Rest day" on the Rest calendar.

    There is no booking tool for this: the prompt has the model offer "an
    all-day 'Rest day' on the Rest calendar", which it books with
    create_allday_event. This does the same.
    """
    try:
        day = _date.fromisoformat(body.day)
    except ValueError:
        raise ApiError(400, "bad_date", "day must be YYYY-MM-DD.")
    try:
        cal = _calendar()
        ev = cal.create_allday_event("Rest day", day.isoformat(), calendar_name="Rest")
    except Exception as e:
        raise ApiError(502, "calendar_error", str(e) or "Google Calendar didn't take the rest day.")
    if not ev:
        raise ApiError(502, "calendar_error", "Google Calendar didn't take the rest day.")
    where = ev.get("calendar") or "Rest"
    return {"ok": True, "message": f"{day:%A, %B} {day.day} is blocked as a rest day on your {where} calendar."}

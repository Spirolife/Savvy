"""
Rest-day early warning — spot a crash-out coming and suggest a rest day first.

The user has a crash-out day roughly weekly when they run out of energy. This
looks for the two warning signs they asked for, and ONLY those:

  1. Bad days they explicitly logged (diary entry_type 'bad_day', written by
     the log_bad_day tool when they say a day was bad). A skipped morning plan
     or evening reflection never counts: they sometimes skip logging, and a
     missing log is not a bad day.
  2. A packed stretch: 3+ consecutive days with almost no free time.

Either trigger looks a few days ahead for a day that can actually be a rest
day. A rest day isn't "do nothing": it's a stay-home day — chores, low-key
errands, and hobbies from the Todoist backlog — with no new commitments.

The detection is plain code so the triggers behave exactly as specified; the
model only writes the suggestion. Tunable via config key "rest_days".
"""

from datetime import datetime, timedelta, timezone

import diary

DEFAULTS = {
    "bad_days_threshold": 2,     # explicitly logged bad days ...
    "bad_days_window": 5,        # ... within this many days (today included)
    "streak_days": 3,            # consecutive packed days
    "full_day_free_hours": 2.0,  # a day is "packed" with less free time than this
    "day_start": "09:00",        # waking window that free time is measured in
    "day_end": "21:00",
    "lookahead_days": 7,         # how far ahead to look for a packed stretch
    "rest_free_hours": 6.0,      # a rest-day candidate needs at least this much open
    "rest_search_days": 4,       # ... within this many days after the trigger
    "check_time": "19:30",       # when the scheduler runs the check
}

# Calendars whose events don't use up the day: sleep/routine, maybe-events.
NOT_BUSY_CALENDARS = {"rest", "optional"}


def settings(config: dict | None = None) -> dict:
    if config is None:
        from core import load_app_config
        config = load_app_config()
    return {**DEFAULTS, **(config.get("rest_days") or {})}


def _window(day, s) -> tuple[datetime, datetime]:
    from calendar_integration import local_day_start
    start = local_day_start(day)
    h1, m1 = map(int, s["day_start"].split(":"))
    h2, m2 = map(int, s["day_end"].split(":"))
    return start.replace(hour=h1, minute=m1), start.replace(hour=h2, minute=m2)


def day_loads(first_day, n_days: int, s: dict) -> list[dict]:
    """Busy/free hours per local day, from one calendar fetch."""
    from calendar_integration import _fetch_events, local_day_start
    lo = local_day_start(first_day)
    hi = local_day_start(lo.date() + timedelta(days=n_days))
    events = _fetch_events(lo.astimezone(timezone.utc), hi.astimezone(timezone.utc), max_results=250)

    out = []
    for i in range(n_days):
        day = lo.date() + timedelta(days=i)
        w0, w1 = _window(day, s)
        spans, labels, whole_day = [], [], False
        for e in events:
            if (e.get("calendar") or "").lower() in NOT_BUSY_CALENDARS:
                continue
            if e.get("is_allday"):
                # A multi-day all-day event is a trip and fills the day; a
                # single-day one is a marker (deadline, holiday) and doesn't.
                first, last = e["start"].date(), (e["end"] - timedelta(days=1)).date()
                if last > first and first <= day <= last:
                    whole_day = True
                    labels.append(e["summary"])
                continue
            a, b = max(e["start"], w0), min(e["end"], w1)
            if a < b:
                spans.append((a, b))
                labels.append(e["summary"])
        spans.sort()
        busy, cur = 0.0, None
        for a, b in spans:                      # merge overlaps
            if cur and a <= cur[1]:
                cur = (cur[0], max(cur[1], b))
            else:
                if cur:
                    busy += (cur[1] - cur[0]).total_seconds()
                cur = (a, b)
        if cur:
            busy += (cur[1] - cur[0]).total_seconds()
        window_h = (w1 - w0).total_seconds() / 3600
        busy_h = window_h if whole_day else busy / 3600
        out.append({"date": day.isoformat(), "weekday": day.strftime("%A"),
                    "busy_hours": round(busy_h, 1), "free_hours": round(window_h - busy_h, 1),
                    "packed": window_h - busy_h < s["full_day_free_hours"],
                    "events": labels})
    return out


def _rest_candidate(loads: list[dict], start_index: int, s: dict) -> dict | None:
    for d in loads[start_index:start_index + s["rest_search_days"]]:
        if d["free_hours"] >= s["rest_free_hours"]:
            return d
    return None


def assess(today=None, config: dict | None = None) -> dict:
    """{"trigger": "bad_days" | "packed_stretch" | None, ..., "rest_day": {...} | None}."""
    s = settings(config)
    today = today or datetime.now().date()

    since = (today - timedelta(days=s["bad_days_window"] - 1)).isoformat()
    bad = diary.get_entries_of_type("bad_day", since)
    bad_dates = sorted({b["date"] for b in bad})

    horizon = s["lookahead_days"] + s["rest_search_days"] + 1
    loads = day_loads(today, horizon, s)

    result = {"trigger": None, "bad_days": [{"date": b["date"], "note": b["content"]} for b in bad],
              "packed_stretch": [], "rest_day": None}

    if len(bad_dates) >= s["bad_days_threshold"]:
        result["trigger"] = "bad_days"
        result["rest_day"] = _rest_candidate(loads, 1, s)       # from tomorrow
        return result

    run = []
    for i, d in enumerate(loads[1:s["lookahead_days"] + 1], start=1):   # upcoming days
        if d["packed"]:
            run.append(i)
            if len(run) >= s["streak_days"]:
                # Extend to the end of the stretch, then look just after it.
                j = i + 1
                while j < len(loads) and loads[j]["packed"]:
                    run.append(j)
                    j += 1
                result["trigger"] = "packed_stretch"
                result["packed_stretch"] = [loads[k] for k in run]
                result["rest_day"] = _rest_candidate(loads, j, s)
                return result
        else:
            run = []
    return result


def hobby_ideas(limit: int = 6) -> list[str]:
    """A few hobbies / small projects from the Todoist backlog, for a rest day."""
    try:
        from todoist_integration import list_tasks
        tasks = list_tasks(filter_query="#Personal Projects | #Fun", max_results=40)
    except Exception:
        return []
    # Small and low-energy ones first: a rest day isn't for the big projects.
    tasks.sort(key=lambda t: not ({"small", "low-energy", "15min"} & set(t.get("labels") or [])))
    return [t["content"] for t in tasks[:limit]]


def format_assessment(a: dict, with_hobbies: bool = True) -> str:
    if not a["trigger"]:
        return "No rest-day warning: fewer logged bad days than the threshold and no packed stretch ahead."
    lines = []
    if a["trigger"] == "bad_days":
        lines.append("Explicitly logged bad days: " + "; ".join(f"{b['date']} ({b['note'][:80]})" for b in a["bad_days"]))
    else:
        lines.append("Packed stretch ahead: " + "; ".join(
            f"{d['weekday']} {d['date']} ({d['free_hours']}h free)" for d in a["packed_stretch"]))
    rd = a["rest_day"]
    if rd:
        lines.append(f"Best rest-day candidate: {rd['weekday']} {rd['date']} — {rd['free_hours']}h open"
                     + (f"; fixed: {', '.join(rd['events'])}" if rd["events"] else ""))
    else:
        lines.append("No day in the next few with enough open time for a full rest day — suggest lightening one instead.")
    hobbies = hobby_ideas() if with_hobbies else []
    if hobbies:
        lines.append("Hobby ideas from Todoist: " + "; ".join(hobbies))
    # The framing travels with the result: in testing, a prompt rule alone
    # still produced "take a low-key day", which isn't what the user wants.
    lines.append("When suggesting it: a rest day is a stay-home day — chores, low-key errands, and one or two "
                 "specific hobbies/small projects (from the list above) — "
                 "not 'do nothing'. Offer to block it; don't book it.")
    return "\n".join(lines)

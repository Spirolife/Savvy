"""
Weekly meal planner — what to cook each week, plus a backlog of recipe ideas.

The user cooks 2-3 full meals and 1-2 drinks a week, and 3 meals + 2 drinks
isn't sustainable, so the realistic weeks are 2+2 or 3+1. Those limits are
enforced HERE rather than only in the prompt: adding past them fails with an
explanation unless the call explicitly says the user insisted. A prose rule is
a suggestion the model can talk itself past; a refusal it has to relay.

Weeks run Monday-Sunday and are identified by their Monday. A meal with no
week is an idea in the backlog, to be slotted into a week later.

Meals belong to a week, not a day: the user decides when to cook and puts
it on the calendar themselves. (The `day` column exists but the tools never set it.)

A meal is one main plus 0-2 sides (the user wants 1-2; a main that's already a
complete plate may stand alone, but the tool warns). Balance — protein and lots
of vegetables, carbs optional — is judged by the model, which is told so in the
prompt and the tool descriptions.

Storage: the local SQLite database (meals) and memory/meal_settings.json.
"""

import json
import sqlite3
import time
from datetime import date, datetime, timedelta

from paths import DB_PATH, MEMORY_DIR

SETTINGS_PATH = MEMORY_DIR / "meal_settings.json"
KINDS = ("meal", "drink")
STATUSES = ("planned", "cooked", "skipped")
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

DEFAULT_SETTINGS = {
    "max_meals": 3,          # full meals per week
    "max_drinks": 2,         # drinks per week
    "max_total": 4,          # meals + drinks: 2+2 and 3+1 fit, 3+2 doesn't
    "reminder_day": "Sunday",
    "reminder_time": "17:00",
    "temporary_diet": "",    # e.g. "low-FODMAP"
    "diet_until": "",        # YYYY-MM-DD; the diet is ignored after this date
}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def get_settings() -> dict:
    try:
        saved = json.loads(SETTINGS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        saved = {}
    return {**DEFAULT_SETTINGS, **saved}


def update_settings(**changes) -> dict:
    settings = get_settings()
    for key, value in changes.items():
        if value is None or key not in DEFAULT_SETTINGS:
            continue
        if key == "reminder_day":
            value = _day_name(value)
        elif key == "reminder_time":
            datetime.strptime(value, "%H:%M")
        elif key == "diet_until" and value:
            datetime.strptime(value, "%Y-%m-%d")
        elif key.startswith("max_"):
            value = int(value)
        settings[key] = value
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")
    return settings


def active_diet(today: date | None = None) -> str:
    """The temporary diet, if one is set and hasn't ended."""
    s = get_settings()
    if not s["temporary_diet"]:
        return ""
    until = s["diet_until"]
    today = today or datetime.now().date()
    if until and datetime.strptime(until, "%Y-%m-%d").date() < today:
        return ""
    return s["temporary_diet"] + (f" (until {until})" if until else "")


def _day_name(value: str) -> str:
    v = (value or "").strip().lower()
    for d in DAYS:
        if d.lower().startswith(v[:3]) and v:
            return d
    raise ValueError(f"Not a weekday: {value!r}")


# ---------------------------------------------------------------------------
# Weeks
# ---------------------------------------------------------------------------
def week_start(week: str | None, today: date | None = None) -> str | None:
    """Resolve "this" / "next" / "+N" / any YYYY-MM-DD in the week → its Monday.

    None or "idea"/"backlog" means no week (the idea backlog).
    """
    if week is None or str(week).strip().lower() in ("", "idea", "ideas", "backlog", "later"):
        return None
    today = today or datetime.now().date()
    w = str(week).strip().lower()
    monday = today - timedelta(days=today.weekday())
    if w in ("this", "this week", "current"):
        offset = 0
    elif w in ("next", "next week"):
        offset = 1
    elif w.lstrip("+").isdigit():
        offset = int(w.lstrip("+"))
    else:
        d = datetime.strptime(w, "%Y-%m-%d").date()
        return (d - timedelta(days=d.weekday())).isoformat()
    return (monday + timedelta(weeks=offset)).isoformat()


def reminder_week(today: date | None = None) -> str:
    """The week a reminder sent today is about: the one starting tomorrow-or-earlier.

    Sunday → the coming Monday's week; any other day → the current week.
    """
    today = today or datetime.now().date()
    return week_start((today + timedelta(days=1)).isoformat())


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def _db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT,                     -- Monday, YYYY-MM-DD; NULL = idea backlog
            kind TEXT NOT NULL DEFAULT 'meal',   -- meal | drink
            main TEXT NOT NULL,
            sides TEXT NOT NULL DEFAULT '[]',    -- JSON list, 0-2
            day TEXT,                            -- optional weekday name
            notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'planned',
            created_at REAL NOT NULL
        )
    """)
    db.commit()
    return db


def _to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["sides"] = json.loads(d["sides"] or "[]")
    return d


def get_week(week: str | None, today: date | None = None) -> list[dict]:
    ws = week_start(week, today)
    db = _db()
    try:
        if ws is None:
            rows = db.execute("SELECT * FROM meals WHERE week_start IS NULL ORDER BY created_at").fetchall()
        else:
            rows = db.execute("SELECT * FROM meals WHERE week_start = ? ORDER BY kind DESC, id", (ws,)).fetchall()
        return [_to_dict(r) for r in rows]
    finally:
        db.close()


def _counts(db, ws: str, exclude_id: int | None = None) -> dict:
    rows = db.execute("SELECT kind, COUNT(*) FROM meals WHERE week_start = ? AND status != 'skipped' "
                      "AND id != ? GROUP BY kind", (ws, exclude_id or -1)).fetchall()
    c = {k: n for k, n in rows}
    return {"meal": c.get("meal", 0), "drink": c.get("drink", 0)}


def _limit_problem(counts: dict, kind: str, settings: dict) -> str:
    """Why one more `kind` would overload the week, or ''."""
    meals = counts["meal"] + (kind == "meal")
    drinks = counts["drink"] + (kind == "drink")
    if meals > settings["max_meals"]:
        return f"that would be {meals} meals; the goal is at most {settings['max_meals']} a week"
    if drinks > settings["max_drinks"]:
        return f"that would be {drinks} drinks; the goal is at most {settings['max_drinks']} a week"
    if meals + drinks > settings["max_total"]:
        return (f"that would be {meals} meals + {drinks} drinks; more than {settings['max_total']} "
                "a week isn't sustainable (2+2 or 3+1 work)")
    return ""


def _week_with_room(db, kind: str, after: str, settings: dict) -> str:
    d = datetime.strptime(after, "%Y-%m-%d").date()
    for i in range(1, 9):
        ws = (d + timedelta(weeks=i)).isoformat()
        if not _limit_problem(_counts(db, ws), kind, settings):
            return ws
    return ""


def _check_sides(kind: str, sides: list[str]) -> str:
    if kind == "meal" and len(sides) > 2:
        raise ValueError("A meal is one main plus 1-2 sides — pick the best two.")
    if kind == "meal" and not sides:
        return ("No sides. Only fine if the main already has protein and plenty of vegetables; "
                "otherwise add 1-2 vegetable-forward sides.")
    return ""


def add_meal(main: str, kind: str = "meal", sides: list[str] | None = None,
             week: str | None = None, day: str | None = None, notes: str = "",
             allow_over_limit: bool = False, today: date | None = None) -> dict:
    kind = (kind or "meal").lower()
    if kind not in KINDS:
        raise ValueError("kind must be 'meal' or 'drink'")
    main = " ".join((main or "").split())
    if not main:
        raise ValueError("A meal needs a main dish (or a drink name).")
    sides = [s.strip() for s in (sides or []) if s and s.strip()] if kind == "meal" else []
    warning = _check_sides(kind, sides)
    ws = week_start(week, today)
    settings = get_settings()
    db = _db()
    try:
        if ws is not None and not allow_over_limit:
            problem = _limit_problem(_counts(db, ws), kind, settings)
            if problem:
                planned = [f"{m['main']} ({m['kind']})" for m in map(_to_dict, db.execute(
                    "SELECT * FROM meals WHERE week_start = ? AND status != 'skipped'", (ws,)))]
                return {"success": False, "added": False, "over_limit": True,
                        "message": f"Not added — {problem}. Tell the user before adding more.",
                        "week_of": ws, "already_planned": planned,
                        "next_week_with_room": _week_with_room(db, kind, ws, settings),
                        "options": "slot it into next_week_with_room, save it as an idea (no week), "
                                   "swap out something already planned, or retry with "
                                   "allow_over_limit=true if the user insists"}
        cur = db.execute(
            "INSERT INTO meals (week_start, kind, main, sides, day, notes, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'planned', ?)",
            (ws, kind, main, json.dumps(sides), _day_name(day) if day else None, notes.strip(), time.time()))
        db.commit()
        out = {"success": True, "added": True, "meal": _to_dict(db.execute(
            "SELECT * FROM meals WHERE id = ?", (cur.lastrowid,)).fetchone()),
               "week_of": ws or "idea backlog"}
        if warning:
            out["warning"] = warning
        if ws:
            out["week_now_has"] = _counts(db, ws)
        return out
    finally:
        db.close()


def update_meal(meal_id: int, main: str | None = None, sides: list[str] | None = None,
                week: str | None = None, day: str | None = None, status: str | None = None,
                notes: str | None = None, allow_over_limit: bool = False,
                today: date | None = None) -> dict:
    """Edit a meal, move it to another week ("idea" sends it back to the backlog), or mark it."""
    db = _db()
    try:
        row = db.execute("SELECT * FROM meals WHERE id = ?", (meal_id,)).fetchone()
        if not row:
            raise ValueError(f"No meal with id {meal_id}. Call get_meal_plan for real ids.")
        meal = _to_dict(row)
        changes = {}
        if main is not None:
            changes["main"] = " ".join(main.split())
        if sides is not None:
            sides = [s.strip() for s in sides if s and s.strip()]
            _check_sides(meal["kind"], sides)
            changes["sides"] = json.dumps(sides)
        if week is not None:
            ws = week_start(week, today)
            if ws and ws != meal["week_start"] and not allow_over_limit:
                problem = _limit_problem(_counts(db, ws, exclude_id=meal_id), meal["kind"], get_settings())
                if problem:
                    return {"success": False, "over_limit": True, "week_of": ws,
                            "message": f"Not moved — {problem}. Tell the user before overloading the week."}
            changes["week_start"] = ws
        if day is not None:
            changes["day"] = _day_name(day) if day else None
        if status is not None:
            if status not in STATUSES:
                raise ValueError(f"status must be one of {', '.join(STATUSES)}")
            changes["status"] = status
        if notes is not None:
            changes["notes"] = notes
        if changes:
            sets = ", ".join(f"{k} = ?" for k in changes)
            db.execute(f"UPDATE meals SET {sets} WHERE id = ?", (*changes.values(), meal_id))
            db.commit()
        return {"success": True, "meal": _to_dict(db.execute(
            "SELECT * FROM meals WHERE id = ?", (meal_id,)).fetchone())}
    finally:
        db.close()


def remove_meal(meal_id: int) -> dict:
    db = _db()
    try:
        row = db.execute("SELECT * FROM meals WHERE id = ?", (meal_id,)).fetchone()
        if not row:
            raise ValueError(f"No meal with id {meal_id}.")
        db.execute("DELETE FROM meals WHERE id = ?", (meal_id,))
        db.commit()
        return {"success": True, "removed": _to_dict(row)["main"]}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def _fmt_meal(m: dict) -> str:
    line = f"- {m['main']}"
    if m["sides"]:
        line += " + " + " + ".join(m["sides"])
    if m["kind"] == "drink":
        line += " (drink)"
    if m.get("day"):
        line += f" — {m['day']}"
    if m["status"] != "planned":
        line += f" [{m['status']}]"
    if m["notes"]:
        line += f" ({m['notes']})"
    return line + f" [meal_id:{m['id']}]"


def format_week(week: str | None, today: date | None = None) -> str:
    ws = week_start(week, today)
    items = get_week(week, today)
    if ws is None:
        return "Recipe ideas (not scheduled):\n" + ("\n".join(map(_fmt_meal, items)) if items else "(none)")
    meals = sum(1 for m in items if m["kind"] == "meal" and m["status"] != "skipped")
    drinks = sum(1 for m in items if m["kind"] == "drink" and m["status"] != "skipped")
    head = f"Week of {datetime.strptime(ws, '%Y-%m-%d'):%b %-d}: {meals} meal(s), {drinks} drink(s)"
    return head + ("\n" + "\n".join(map(_fmt_meal, items)) if items else "\n(nothing planned)")


def format_for_context(today: date | None = None) -> str:
    """This week, next week, the idea count, and an active temporary diet."""
    parts = [format_week("this", today), format_week("next", today)]
    ideas = get_week(None, today)
    if ideas:
        parts.append(f"{len(ideas)} recipe idea(s) in the backlog (get_meal_plan week='idea').")
    diet = active_diet(today)
    if diet:
        parts.append(f"Temporary diet in effect: {diet}.")
    return "\n".join(parts)

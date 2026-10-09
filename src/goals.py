"""
Goal tracker — explicit goals the user has stated, kept as a structured list.

Goals used to live in two places: a hand-written New Year's resolutions block in
the system prompt, and whatever goal-shaped sentences fact extraction happened
to store ("User wants to sleep by midnight", "User has an 11 PM sleep goal" —
both, contradicting each other). Neither could be added to, completed, or
measured. Here each goal is a row with a target and a status, sub-goals hang
off a parent ("5 pull-ups" under "Work out 3x/week"), and progress is a log.

Active goals are injected into every prompt (core.build_context) so Savvy
weighs them in every recommendation without a tool call.

Lives in the same local SQLite database as memory.
"""

import sqlite3
import time
from datetime import datetime

from paths import DB_PATH

STATUSES = ("active", "paused", "done", "dropped")


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            target TEXT NOT NULL DEFAULT '',
            why TEXT NOT NULL DEFAULT '',
            due_date TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            parent_id INTEGER REFERENCES goals(id),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS goal_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL REFERENCES goals(id),
            note TEXT NOT NULL,
            value TEXT NOT NULL DEFAULT '',
            timestamp REAL NOT NULL
        )
    """)
    db.commit()
    return db


def _row(db: sqlite3.Connection, goal_id: int) -> dict | None:
    r = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    return dict(r) if r else None


def add_goal(title: str, category: str = "", target: str = "", why: str = "",
             due_date: str | None = None, parent_id: int | None = None) -> dict:
    """Add a goal. An active goal with the same title is returned instead of duplicated."""
    title = " ".join((title or "").split())
    if not title:
        raise ValueError("A goal needs a title.")
    if due_date:
        datetime.strptime(due_date, "%Y-%m-%d")      # reject malformed dates early
    db = _db()
    try:
        if parent_id is not None and not _row(db, parent_id):
            raise ValueError(f"No goal with id {parent_id} to nest under.")
        dup = db.execute("SELECT id FROM goals WHERE lower(title) = lower(?) AND status = 'active'",
                         (title,)).fetchone()
        if dup:
            return {"duplicate": True, **_row(db, dup["id"])}
        now = time.time()
        cur = db.execute(
            "INSERT INTO goals (title, category, target, why, due_date, status, parent_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)",
            (title, category.strip().lower(), target.strip(), why.strip(), due_date, parent_id, now, now))
        db.commit()
        return _row(db, cur.lastrowid)
    finally:
        db.close()


def update_goal(goal_id: int, **fields) -> dict:
    """Edit title/category/target/why/due_date/status/parent_id. None means leave as is."""
    allowed = {"title", "category", "target", "why", "due_date", "status", "parent_id"}
    changes = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "status" in changes and changes["status"] not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    if changes.get("due_date"):
        datetime.strptime(changes["due_date"], "%Y-%m-%d")
    db = _db()
    try:
        if not _row(db, goal_id):
            raise ValueError(f"No goal with id {goal_id}. Call list_goals for real ids.")
        if changes:
            sets = ", ".join(f"{k} = ?" for k in changes)
            db.execute(f"UPDATE goals SET {sets}, updated_at = ? WHERE id = ?",
                       (*changes.values(), time.time(), goal_id))
            db.commit()
        return _row(db, goal_id)
    finally:
        db.close()


def log_progress(goal_id: int, note: str, value: str = "") -> dict:
    db = _db()
    try:
        goal = _row(db, goal_id)
        if not goal:
            raise ValueError(f"No goal with id {goal_id}. Call list_goals for real ids.")
        now = time.time()
        db.execute("INSERT INTO goal_progress (goal_id, note, value, timestamp) VALUES (?, ?, ?, ?)",
                   (goal_id, note.strip(), str(value or "").strip(), now))
        db.execute("UPDATE goals SET updated_at = ? WHERE id = ?", (now, goal_id))
        db.commit()
        return {"goal": goal["title"], "note": note.strip(), "value": value}
    finally:
        db.close()


def list_goals(status: str | None = "active", category: str | None = None,
               with_progress: int = 3) -> list[dict]:
    """Goals (with their latest `with_progress` progress entries), parents before children."""
    db = _db()
    try:
        sql, args = "SELECT * FROM goals WHERE 1=1", []
        if status and status != "all":
            sql += " AND status = ?"
            args.append(status)
        if category:
            sql += " AND category = ?"
            args.append(category.strip().lower())
        goals = [dict(r) for r in db.execute(sql + " ORDER BY COALESCE(parent_id, id), parent_id IS NOT NULL, id", args)]
        for g in goals:
            g["progress"] = [dict(r) for r in db.execute(
                "SELECT note, value, timestamp FROM goal_progress WHERE goal_id = ? "
                "ORDER BY timestamp DESC LIMIT ?", (g["id"], with_progress))]
        return goals
    finally:
        db.close()


def _ago(ts: float) -> str:
    days = int((time.time() - ts) / 86400)
    return "today" if days < 1 else f"{days}d ago" if days < 60 else f"{days // 30}mo ago"


def format_goals_for_context(goals: list[dict] | None = None) -> str:
    goals = list_goals() if goals is None else goals
    if not goals:
        return "(no goals tracked yet)"
    ids = {g["id"] for g in goals}
    lines = []
    for g in goals:
        indent = "    " if g["parent_id"] in ids else ""
        line = f"{indent}- {g['title']}"
        if g["target"]:
            line += f" — target: {g['target']}"
        if g["due_date"]:
            line += f" (by {g['due_date']})"
        if g["status"] != "active":
            line += f" [{g['status']}]"
        line += f" [goal_id:{g['id']}]"
        if g["why"]:
            line += f"\n{indent}    why: {g['why']}"
        if g.get("progress"):
            p = g["progress"][0]
            line += f"\n{indent}    latest: {p['note']}" + (f" ({p['value']})" if p["value"] else "") + f", {_ago(p['timestamp'])}"
        lines.append(line)
    return "\n".join(lines)

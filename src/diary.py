"""
Diary / journal system for the private secretary.
Stores morning plans (BOD) and evening reflections (EOD) locally.
The scheduler uses these to track goals and give better recommendations.
"""

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from paths import DB_PATH


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS diary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_type TEXT NOT NULL,
            content TEXT NOT NULL,
            llm_response TEXT,
            date TEXT NOT NULL,
            timestamp REAL NOT NULL
        )
    """)
    db.commit()
    return db


def store_entry(entry_type: str, content: str, llm_response: str = "") -> int:
    """Store a diary entry. entry_type is 'bod', 'eod', or 'note'."""
    db = _get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor = db.execute(
        """INSERT INTO diary (entry_type, content, llm_response, date, timestamp)
           VALUES (?, ?, ?, ?, ?)""",
        (entry_type, content, llm_response, today, time.time()),
    )
    db.commit()
    entry_id = cursor.lastrowid
    db.close()
    return entry_id


def get_today_entries() -> list[dict]:
    """Get all diary entries for today."""
    db = _get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    rows = db.execute(
        "SELECT entry_type, content, llm_response, timestamp FROM diary WHERE date = ? ORDER BY timestamp",
        (today,),
    ).fetchall()
    db.close()
    return [
        {"type": t, "content": c, "response": r, "timestamp": ts}
        for t, c, r, ts in rows
    ]


def get_entries_for_date(date_str: str) -> list[dict]:
    """Get diary entries for a specific date (YYYY-MM-DD)."""
    db = _get_db()
    rows = db.execute(
        "SELECT entry_type, content, llm_response, timestamp FROM diary WHERE date = ? ORDER BY timestamp",
        (date_str,),
    ).fetchall()
    db.close()
    return [
        {"type": t, "content": c, "response": r, "timestamp": ts}
        for t, c, r, ts in rows
    ]


def get_recent_entries(days: int = 7) -> list[dict]:
    """Get diary entries from the last N days."""
    db = _get_db()
    cutoff = time.time() - (days * 86400)
    rows = db.execute(
        """SELECT entry_type, content, llm_response, date, timestamp
           FROM diary WHERE timestamp > ? ORDER BY timestamp""",
        (cutoff,),
    ).fetchall()
    db.close()
    return [
        {"type": t, "content": c, "response": r, "date": d, "timestamp": ts}
        for t, c, r, d, ts in rows
    ]


def has_entry_today(entry_type: str) -> bool:
    """Check if a BOD or EOD entry already exists for today."""
    db = _get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    row = db.execute(
        "SELECT COUNT(*) FROM diary WHERE date = ? AND entry_type = ?",
        (today, entry_type),
    ).fetchone()
    db.close()
    return row[0] > 0


def get_weekly_summary_data() -> dict:
    """Get structured data for a weekly review."""
    db = _get_db()
    week_ago = time.time() - (7 * 86400)

    bod_entries = db.execute(
        "SELECT content, date FROM diary WHERE entry_type = 'bod' AND timestamp > ? ORDER BY timestamp",
        (week_ago,),
    ).fetchall()

    eod_entries = db.execute(
        "SELECT content, date FROM diary WHERE entry_type = 'eod' AND timestamp > ? ORDER BY timestamp",
        (week_ago,),
    ).fetchall()

    # Days with entries vs days without
    dates_with_bod = set(d for _, d in bod_entries)
    dates_with_eod = set(d for _, d in eod_entries)

    db.close()

    return {
        "bod_entries": [{"content": c, "date": d} for c, d in bod_entries],
        "eod_entries": [{"content": c, "date": d} for c, d in eod_entries],
        "days_with_bod": len(dates_with_bod),
        "days_with_eod": len(dates_with_eod),
        "total_days": 7,
    }


def format_entries_for_context(entries: list[dict]) -> str:
    """Format diary entries for LLM context."""
    if not entries:
        return "(no diary entries)"

    lines = []
    for e in entries:
        date = e.get("date", "")
        ts = datetime.fromtimestamp(e["timestamp"]).strftime("%-I:%M %p")
        label = {"bod": "Morning Plan", "eod": "Evening Reflection", "note": "Note"}.get(
            e["type"], e["type"]
        )
        lines.append(f"[{date} {ts}] {label}: {e['content'][:300]}")

    return "\n".join(lines)
"""
One-off scheduled reminders — e.g. "check in with me at 5pm today, and if
I've done my PT, add a rest day to the calendar tomorrow."

Unlike the fixed BOD/EOD/check-in times in config.json, these are ad-hoc,
created by the LLM via the schedule_reminder tool at the user's request, and
fire exactly once. scheduler.py polls for due ones and runs their
instruction through the full tool-calling loop (not just a canned message),
so conditional instructions can actually check calendar/diary/etc. and take
real actions before notifying the user.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paths import MEMORY_DIR

REMINDERS_PATH = MEMORY_DIR / "scheduled_reminders.json"

# Reminders overdue by more than this are dropped rather than fired late
# (e.g. after a multi-day outage) — a stale surprise check-in isn't useful.
MAX_OVERDUE = timedelta(hours=24)


def _load() -> list[dict]:
    if REMINDERS_PATH.exists():
        with open(REMINDERS_PATH) as f:
            return json.load(f)
    return []


def _save(reminders: list[dict]) -> None:
    with open(REMINDERS_PATH, "w") as f:
        json.dump(reminders, f, indent=2)


def add_reminder(when_iso: str, instruction: str) -> dict:
    """Schedule a new one-off reminder. `when_iso` must be a real ISO datetime."""
    reminder = {
        "id": uuid.uuid4().hex[:8],
        "due_at": when_iso,
        "instruction": instruction,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    reminders = _load()
    reminders.append(reminder)
    _save(reminders)
    return reminder


def remove_reminder(reminder_id: str) -> dict | None:
    """Delete a pending reminder. Returns it, or None if no such id is pending."""
    reminders = _load()
    hit = next((r for r in reminders if r["id"] == reminder_id), None)
    if hit:
        _save([r for r in reminders if r["id"] != reminder_id])
    return hit


def update_reminder(reminder_id: str, when_iso: str | None = None,
                    instruction: str | None = None) -> dict | None:
    """Change a pending reminder's time and/or instruction. None if not found."""
    reminders = _load()
    hit = next((r for r in reminders if r["id"] == reminder_id), None)
    if hit:
        if when_iso:
            hit["due_at"] = when_iso
        if instruction:
            hit["instruction"] = instruction
        _save(reminders)
    return hit


def list_pending() -> list[dict]:
    """All reminders not yet due/fired, soonest first."""
    reminders = sorted(_load(), key=lambda r: r["due_at"])
    return reminders


def pop_due_reminders() -> list[dict]:
    """Remove and return reminders whose due_at has passed (and isn't too
    stale to bother with). Reminders not yet due are left in place."""
    now = datetime.now().astimezone()
    reminders = _load()
    due, remaining = [], []

    for r in reminders:
        try:
            due_at = datetime.fromisoformat(r["due_at"])
            if due_at.tzinfo is None:
                due_at = due_at.astimezone()
        except (ValueError, TypeError):
            remaining.append(r)  # unparsable — keep rather than silently drop
            continue

        if due_at > now:
            remaining.append(r)
        elif now - due_at <= MAX_OVERDUE:
            due.append(r)
        # else: too stale, drop silently

    if len(remaining) != len(reminders):
        _save(remaining)

    return due


def requeue(reminder: dict) -> None:
    """Put a reminder back (e.g. it was due but couldn't be sent yet —
    quiet hours, notification budget — so retry next poll)."""
    reminders = _load()
    reminders.append(reminder)
    _save(reminders)

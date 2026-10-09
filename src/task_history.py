"""
Rolling history of write-level tool calls (creates/updates/deletes/moves),
so the `check_history` tool can look up things like the event_id of a
calendar event created earlier in the week before modifying or deleting it —
without needing to re-read the full tool_audit.jsonl audit log.

Read-only lookups (get_calendar_range, search_emails, etc.) aren't logged
here — they're cheap to redo live and would just bloat the weekly files.

Files rotate weekly (ISO year-week) under memory/task_history/, so a
check_history call only ever has to scan a bounded, small number of files
regardless of how long the secretary has been running.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paths import MEMORY_DIR

HISTORY_DIR = MEMORY_DIR / "task_history"


def _week_file(dt: datetime) -> Path:
    year, week, _ = dt.isocalendar()
    return HISTORY_DIR / f"{year}-W{week:02d}.jsonl"


def log_task(name: str, inp: dict, result: str) -> None:
    """Append one record for a write-level tool call to the current week's file."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": name,
        "input": inp,
        "result": result[:500] if isinstance(result, str) else result,
    }
    with open(_week_file(datetime.now(timezone.utc)), "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def check_history(query: str = "", weeks_back: int = 1) -> str:
    """Search the current week's history plus `weeks_back` previous weeks
    for a keyword. Returns matching entries, most recent first."""
    now = datetime.now(timezone.utc)
    seen: set[Path] = set()
    files = []
    for i in range(max(weeks_back, 0) + 1):
        f = _week_file(now - timedelta(weeks=i))
        if f.exists() and f not in seen:
            seen.add(f)
            files.append(f)

    entries = []
    for f in files:
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    if query:
        q = query.lower()
        entries = [
            e for e in entries
            if q in e.get("tool", "").lower()
            or q in json.dumps(e.get("input", {}), default=str).lower()
            or q in str(e.get("result", "")).lower()
        ]

    if not entries:
        return "(no matching history)"

    lines = []
    for e in entries[:20]:
        args = ", ".join(f"{k}={v!r}" for k, v in e.get("input", {}).items())
        result_preview = str(e.get("result", ""))[:200]
        lines.append(f"[{e.get('timestamp', '?')}] {e.get('tool', '?')}({args}) → {result_preview}")
    return "\n".join(lines)

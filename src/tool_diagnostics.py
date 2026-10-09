"""
Tool-call diagnostics with danger-level-driven confirmation.

Danger levels (from credentials/tool_config.json):
    0  read-only         — run silently, print one dim line
    1  internal write    — print one line from the result; prompt (with the
                           full call) ONLY if LLM flagged `_importance: "high"`
    2  external write    — print full call, ALWAYS prompt before running

Tools not present in the config file default to level 2 (fail-safe).

The LLM cannot de-escalate. A level-2 call is always prompted regardless
of `_importance`. The flag only matters within level 1, where the LLM
can request human confirmation for unusual calls (e.g. deleting a
recurring meeting) that wouldn't otherwise be prompted.

All calls — printed, prompted, or denied — are appended to a JSONL audit
log for after-the-fact review.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from paths import CREDENTIALS_DIR, MEMORY_DIR

CONFIG_PATH = CREDENTIALS_DIR / "tool_config.json"
AUDIT_LOG = MEMORY_DIR / "tool_audit.jsonl"

DEFAULT_DANGER = 2  # Fail-safe for tools not in the config file.

# Internal mutable state. Reloadable from disk via reload_config().
_danger_levels: dict[str, int] = {}
_session_allowlist: set[str] = set()


# ANSI colors
DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
RESET = "\033[0m"

LEVEL_LABELS = {0: "read", 1: "internal", 2: "external"}


def _tag(level: int) -> str:
    return f"[L{level} {LEVEL_LABELS[level]}]"
LEVEL_COLORS = {0: CYAN, 1: YELLOW, 2: RED}


# ---------------------------------------------------------------------------
# Config loading / saving
# ---------------------------------------------------------------------------
def reload_config() -> None:
    """(Re)load danger levels and session allowlist from disk."""
    global _danger_levels, _session_allowlist
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        _danger_levels = {k: int(v) for k, v in data.get("danger_levels", {}).items()}
        _session_allowlist = set(data.get("session_allowlist", []))
    except FileNotFoundError:
        print(f"{YELLOW}[diagnostics] {CONFIG_PATH} not found — all tools default to level {DEFAULT_DANGER}.{RESET}")
        _danger_levels = {}
        _session_allowlist = set()
    except (json.JSONDecodeError, ValueError) as e:
        print(f"{RED}[diagnostics] Bad config: {e} — all tools default to level {DEFAULT_DANGER}.{RESET}")
        _danger_levels = {}
        _session_allowlist = set()


def save_config() -> None:
    """Persist current state back to disk."""
    try:
        # Preserve any leading comment field if it was there.
        existing: dict = {}
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH) as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        existing["danger_levels"] = dict(sorted(_danger_levels.items()))
        existing["session_allowlist"] = sorted(_session_allowlist)
        with open(CONFIG_PATH, "w") as f:
            json.dump(existing, f, indent=2)
    except OSError as e:
        print(f"{RED}[diagnostics] Failed to save config: {e}{RESET}")


def get_danger_level(name: str) -> int:
    return _danger_levels.get(name, DEFAULT_DANGER)


def set_danger_level(name: str, level: int) -> None:
    if level not in (0, 1, 2):
        raise ValueError(f"Level must be 0, 1, or 2 (got {level})")
    _danger_levels[name] = level
    save_config()


def add_to_allowlist(name: str) -> None:
    _session_allowlist.add(name)
    save_config()


def remove_from_allowlist(name: str) -> None:
    _session_allowlist.discard(name)
    save_config()


def snapshot_config() -> dict:
    """Return a copy of current config for display."""
    return {
        "danger_levels": dict(sorted(_danger_levels.items())),
        "session_allowlist": sorted(_session_allowlist),
    }


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def _json_default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def _audit_write(record: dict) -> None:
    try:
        with open(AUDIT_LOG, "a") as f:
            f.write(json.dumps(record, default=_json_default) + "\n")
    except Exception as e:
        print(f"{DIM}[audit] Failed to write log: {e}{RESET}")


def log_session_start() -> None:
    _audit_write({
        "phase": "session_start",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    print(f"{DIM}[audit] Logging tool calls to {AUDIT_LOG}{RESET}")


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
def _print_full(name: str, inp: dict, call_id: str, level: int) -> None:
    """Bordered full-detail print for level 1/2 calls."""
    color = LEVEL_COLORS[level]
    label = LEVEL_LABELS[level]
    importance = inp.get("_importance")
    importance_tag = f"  {BOLD}{RED}[!important]{RESET}{color}" if importance == "high" else ""
    print(f"\n{color}┌─ {BOLD}level {level} ({label}){RESET}{color}{importance_tag}  {BOLD}{name}{RESET}{color}  [{call_id}]{RESET}")
    shown = {k: v for k, v in inp.items() if k != "_importance"}
    if shown:
        for k, v in shown.items():
            if isinstance(v, str):
                if "\n" in v:
                    print(f"{color}│{RESET}  {BOLD}{k}{RESET}:")
                    for line in v.splitlines():
                        print(f"{color}│{RESET}    {line}")
                else:
                    # repr() makes whitespace, empty strings, unusual chars visible
                    print(f"{color}│{RESET}  {BOLD}{k}{RESET}: {v!r}")
            else:
                print(f"{color}│{RESET}  {BOLD}{k}{RESET}: {v}")
    else:
        print(f"{color}│{RESET}  {DIM}(no arguments){RESET}")


def _prompt(name: str, inp: dict, call_id: str, level: int) -> str:
    """Block until user decides. Returns 'approve', 'deny', or 'always'."""
    while True:
        try:
            answer = input(
                f"{BOLD}{LEVEL_COLORS[level]}│  Execute? [y]es / [n]o / [a]lways / [s]how  (default: no): {RESET}"
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{RED}│  Interrupted — denying call.{RESET}")
            return "deny"

        if answer in ("y", "yes"):
            return "approve"
        if answer in ("", "n", "no"):
            return "deny"
        if answer in ("a", "always"):
            return "always"
        if answer in ("s", "show"):
            _print_full(name, inp, call_id, level)
            continue
        print(f"{DIM}│  Type y, n, a, or s.{RESET}")


# ---------------------------------------------------------------------------
# Main entry points used by tools.py
# ---------------------------------------------------------------------------
def review_tool_call(name: str, inp: dict) -> tuple[str, bool]:
    """Display the call, prompt if needed, return (call_id, approved).

    Decision logic:
        level 0 → approve
        level 1 → approve, unless the LLM flagged _importance=high and the
                  tool is not on the session allowlist: then print full and prompt
        level 2 → print full; always prompt unless on session allowlist
    """
    call_id = uuid.uuid4().hex[:8]
    level = get_danger_level(name)
    importance = inp.get("_importance")

    _audit_write({
        "call_id": call_id, "phase": "call",
        "tool": name, "level": level,
        "importance_flag": importance,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input": inp,
    })

    # Calls that run without asking print nothing now; log_tool_result prints
    # one line from what the tool actually returned. The full call is in the
    # audit log.
    if level == 0:
        return call_id, True

    if name in _session_allowlist:
        _audit_write({"call_id": call_id, "phase": "decision",
                      "tool": name, "decision": "auto_allowlist"})
        return call_id, True

    # Level 1: prompt only if LLM escalated
    if level == 1 and importance != "high":
        _audit_write({"call_id": call_id, "phase": "decision",
                      "tool": name, "decision": "auto_level1"})
        return call_id, True

    # Otherwise: show the full call and prompt the human
    _print_full(name, inp, call_id, level)
    decision = _prompt(name, inp, call_id, level)
    _audit_write({"call_id": call_id, "phase": "decision",
                  "tool": name, "decision": decision})

    if decision == "always":
        add_to_allowlist(name)
        print(f"{GREEN}│  '{name}' added to session allowlist.{RESET}")
        return call_id, True
    if decision == "approve":
        return call_id, True
    print(f"{RED}│  Call denied.{RESET}")
    return call_id, False


def _when(iso, date_only: bool = False) -> str:
    """'2026-10-06T08:15:00-04:00' -> 'Tue Oct 6, 8:15 am'; a bare date -> 'Tue Oct 6'."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    if len(str(iso)) <= 10 or date_only:
        return dt.strftime("%a %b %-d")
    dt = _local(dt)
    return f"{dt.strftime('%a %b %-d')}, {dt.strftime('%-I:%M %p').lower()}"


def _local(dt: datetime) -> datetime:
    """In the user's configured timezone (Google may hand back UTC)."""
    if dt.tzinfo is None:
        return dt
    try:
        from calendar_integration import user_tz
        return dt.astimezone(user_tz())
    except Exception:
        return dt.astimezone()


def _short(text, limit: int = 90) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _event_line(ev: dict) -> str:
    when = _when(ev.get("start"), date_only=bool(ev.get("is_allday")))
    if ev.get("end") and not ev.get("is_allday"):
        try:
            when += "–" + _local(datetime.fromisoformat(str(ev["end"]))).strftime("%-I:%M %p").lower()
        except ValueError:
            pass
    cal = f"  ({ev['calendar']})" if ev.get("calendar") else ""
    repeats = "  ↻ repeats" if ev.get("recurrence") else ""
    return f"{ev.get('summary', '')} · {when}{cal}{repeats}"


def _first_task_line(text) -> str:
    """format_tasks_for_context output -> the first task, minus its task_id
    (priority, labels and project stay)."""
    line = str(text or "").strip().splitlines()[0] if text else ""
    return _short(line.split(" [task_id:")[0].lstrip("- "), 120)


def _summarize(name: str, r: dict) -> str:
    """One readable line for a successful write, taken from the tool's result."""
    if name in ("create_calendar_event", "create_allday_event", "quick_add_event") and isinstance(r.get("event"), dict):
        return _event_line(r["event"])
    if name == "delete_calendar_event":
        return f"{r.get('summary', '')} · {_when(r.get('start'))}  ({r.get('calendar', '')})"
    if name == "update_calendar_event":
        return f"{r.get('summary', '')}  ({r.get('calendar', '')})"
    if name == "move_event":
        return r.get("note") or f"{r.get('summary', '')} → {r.get('destination', '')}"
    if name == "schedule_travel_event":
        return f"leave {r.get('leave_at')} → arrive {r.get('arrive_by')} ({r.get('travel_minutes')} min {r.get('mode')})"
    if name in ("schedule_reminder", "update_reminder", "cancel_reminder") and isinstance(r.get("reminder"), dict):
        rem = r["reminder"]
        removed = "removed: " if name == "cancel_reminder" else ""
        return f"{removed}{_when(rem.get('due_at'))} · {_short(rem.get('instruction'))}"
    if name in ("create_task", "update_task", "move_task"):
        return _first_task_line(r.get("task"))
    if name == "save_fact":
        parts = [f"\"{_short(r.get('stored'), 100)}\"",
                 f"{r.get('type', '?')} — {r.get('lifetime', '?')}",
                 "new" if r.get("new") else "refreshed existing"]
        if r.get("replaced"):
            parts.append(f"replaced {len(r['replaced'])}")
        return " · ".join(parts)
    if name in ("add_goal", "update_goal") and isinstance(r.get("goal"), dict):
        g = r["goal"]
        parts = [g.get("title", "")]
        parts += [x for x in (g.get("category"), g.get("target")) if x]
        if g.get("due_date"):
            parts.append(f"due {g['due_date']}")
        if g.get("status") and g["status"] != "active":
            parts.append(g["status"])
        if g.get("duplicate"):
            parts.append("already existed")
        return " · ".join(parts)
    if name == "log_goal_progress":
        value = f" ({r['value']})" if r.get("value") else ""
        return f"{r.get('goal', '')}: {_short(r.get('note'))}{value}"
    if name in ("add_meal", "update_meal") and isinstance(r.get("meal"), dict):
        m = r["meal"]
        sides = f" + {', '.join(m['sides'])}" if m.get("sides") else ""
        week = r.get("week_of") or m.get("week_start") or "idea backlog"
        status = f" · {m['status']}" if m.get("status") and m["status"] != "planned" else ""
        return f"{m.get('main', '')}{sides} · week of {week}{status}"
    if name == "remove_meal":
        return f"removed {r.get('removed', '')}"
    if name == "store_diary_entry":
        return f"{r.get('type', '')} entry"
    if name == "save_travel_time":
        return f"{r.get('from')} → {r.get('to')}: {r.get('minutes')} min {r.get('mode')}"
    if name == "forget_fact":
        return f"deleted {r.get('deleted_count', 0)} fact(s) matching {r.get('pattern')!r}"
    if name == "log_bad_day":
        return f"logged {r.get('logged')}"
    for key in ("summary", "task", "stored", "message", "place", "file", "name", "type"):
        if isinstance(r.get(key), str) and r[key]:
            return _short(r[key])
    for key in ("goal", "meal", "project"):
        v = r.get(key)
        if isinstance(v, dict):
            return _short(v.get("title") or v.get("main") or v.get("name") or "")
    return ""


def log_tool_result(call_id: str, name: str, result: str) -> None:
    """Print one line about the result and log it in full."""
    parsed = None
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        pass

    level = get_danger_level(name)
    failed = isinstance(parsed, dict) and (parsed.get("success") is False or "error" in parsed)

    if failed:
        why = parsed.get("error") or parsed.get("reason") or parsed.get("message") or "failed"
        print(f"{RED}✗ {_tag(level)} {name}{RESET}  {_short(why, 160)}")
    elif level == 0:
        # Reads: just how much came back.
        if isinstance(parsed, (dict, list)):
            size = f"{len(parsed)} item(s)" if isinstance(parsed, list) else "ok"
        else:
            n = len(str(result).strip().splitlines())
            size = _short(result, 60) if n == 1 else f"{n} lines"
        print(f"{DIM}· {_tag(level)} {name}  → {size}{RESET}")
    else:
        detail = _summarize(name, parsed) if isinstance(parsed, dict) else _short(result)
        warn = f"  {YELLOW}⚠ {_short(parsed['warning'], 120)}{RESET}" if isinstance(parsed, dict) and parsed.get("warning") else ""
        print(f"{GREEN}✓{RESET} {LEVEL_COLORS[level]}{_tag(level)} {name}{RESET}  {detail}{warn}")

    _audit_write({
        "call_id": call_id, "phase": "result",
        "tool": name, "result": result,
        "parsed_success": parsed.get("success") if isinstance(parsed, dict) else None,
    })


def print_fact_saved(fact: str, fact_type: str, new: bool) -> None:
    """A fact stored by after-turn extraction, not a tool call — same line format."""
    from memory import normalize_fact_type, fact_lifetime
    fact_type = normalize_fact_type(fact_type)
    status = "new" if new else "refreshed existing"
    print(f"{DIM}✓ [auto] fact  \"{_short(fact, 100)}\" · {fact_type} — {fact_lifetime(fact_type)} · {status}{RESET}")


def log_tool_error(call_id: str, name: str, exc: Exception) -> None:
    print(f"{RED}✗ {_tag(get_danger_level(name))} {name}  {type(exc).__name__}: {_short(exc, 160)}{RESET}")
    _audit_write({
        "call_id": call_id, "phase": "exception",
        "tool": name,
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
    })


def log_denied(call_id: str, name: str) -> str:
    """Build the tool_result payload for a denied call. Returns a string
    suitable for handing back to the LLM as the tool result."""
    msg = "Tool call denied by user. Do not retry without revising the request."
    _audit_write({"call_id": call_id, "phase": "denied",
                  "tool": name, "message": msg})
    return json.dumps({"success": False, "denied": True, "message": msg})


# Load config at import time so callers can use these functions immediately.
reload_config()
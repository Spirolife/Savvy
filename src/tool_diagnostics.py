"""
Tool-call diagnostics with danger-level-driven confirmation.

Danger levels (from credentials/tool_config.json):
    0  read-only         — print one line, run silently
    1  internal write    — print full call; prompt ONLY if LLM flagged
                           `_importance: "high"` in the call
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
def _print_one_line(name: str, inp: dict, call_id: str, level: int) -> None:
    """Compact one-line print for read-only calls."""
    color = LEVEL_COLORS[level]
    # Strip the synthetic _importance flag from display — it's not a real arg.
    shown = {k: v for k, v in inp.items() if k != "_importance"}
    args_str = ", ".join(f"{k}={v!r}" for k, v in shown.items())
    if len(args_str) > 200:
        args_str = args_str[:200] + "..."
    print(f"{color}· {name}{RESET}({DIM}{args_str}{RESET}) [{call_id}]")


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
        level 0 → print one line, approve
        level 1 → print full; prompt only if LLM flagged _importance=high
                  AND tool is not on the session allowlist
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

    # Level 0: silent run, one-line print
    if level == 0:
        _print_one_line(name, inp, call_id, level)
        return call_id, True

    # Level 1 and 2 both get the full bordered print
    _print_full(name, inp, call_id, level)

    # Allowlist bypass
    if name in _session_allowlist:
        print(f"{LEVEL_COLORS[level]}│{RESET}  {DIM}(auto-approved — on session allowlist){RESET}")
        _audit_write({"call_id": call_id, "phase": "decision",
                      "tool": name, "decision": "auto_allowlist"})
        return call_id, True

    # Level 1: prompt only if LLM escalated
    if level == 1 and importance != "high":
        _audit_write({"call_id": call_id, "phase": "decision",
                      "tool": name, "decision": "auto_level1"})
        return call_id, True

    # Otherwise: prompt the human
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


def log_tool_result(call_id: str, name: str, result: str) -> None:
    """Print and log a successful tool result."""
    parsed = None
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        pass

    if isinstance(parsed, dict) and parsed.get("success") is False:
        status = f"{RED}✗ failed{RESET}"
    elif isinstance(parsed, dict) and "error" in parsed:
        status = f"{RED}✗ error{RESET}"
    else:
        status = f"{GREEN}✓ ok{RESET}"

    level = get_danger_level(name)
    color = LEVEL_COLORS[level]

    if level == 0:
        # Compact output for read calls — one indented line.
        preview = result if len(result) <= 200 else result[:200] + f"... ({len(result)} chars)"
        print(f"  {DIM}→ {status} {preview}{RESET}")
    else:
        preview = result if len(result) <= 400 else result[:400] + f"... ({len(result)} chars)"
        print(f"{color}│{RESET}  → {status}")
        print(f"{color}│{RESET}  {DIM}{preview}{RESET}")
        print(f"{color}└─{RESET}")

    _audit_write({
        "call_id": call_id, "phase": "result",
        "tool": name, "result": result,
        "parsed_success": parsed.get("success") if isinstance(parsed, dict) else None,
    })


def log_tool_error(call_id: str, name: str, exc: Exception) -> None:
    print(f"{RED}│  ✗ EXCEPTION: {type(exc).__name__}: {exc}{RESET}")
    print(f"{RED}└─{RESET}")
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
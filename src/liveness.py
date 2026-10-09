#!/usr/bin/env python3
"""
Liveness alerts — tell me over Signal when a Savvy service starts or dies.

Two entry points:

  Startup      signal_bot.py / scheduler.py call announce_up() once they have
               finished initializing and are genuinely serving.

  Crash        systemd calls this module via `OnFailure=savvy-alert@%n.service`
               when a unit enters the failed state. That path deliberately does
               NOT run inside the process that died — a segfault, OOM kill, or
               hard SIGKILL leaves nothing behind to report itself.

Only httpx is needed here (through notifier). This module must stay importable
when anthropic, google-api-python-client, or Ollama are broken — those are
exactly the failures it exists to report.

Alerts are rate-limited per (event, unit) so a crash-loop texts you once with a
running count rather than every RestartSec seconds. Set "liveness_alerts": false
in credentials/config.json to silence all of it.
"""

import json
import subprocess
import sys
import time
from datetime import datetime

from notifier import load_config, send_message
from paths import MEMORY_DIR

STATE_PATH = MEMORY_DIR / "liveness_state.json"
DEFAULT_COOLDOWN_SECONDS = 600  # 10 min between repeats of the same alert
JOURNAL_LINES = 12


# ---------------------------------------------------------------------------
# Cooldown state
# ---------------------------------------------------------------------------
def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")
    except OSError:
        pass  # An unwritable state file must not stop the alert itself.


def _check_cooldown(key: str, cooldown: int) -> tuple[bool, int]:
    """Return (should_send, suppressed_count_since_last_send).

    Only *reads* state and records suppressions. The cooldown clock is started
    by _record_sent, and only on a send that actually succeeded — otherwise a
    failed delivery (container down, untrusted identity) would silence the
    retry for a full cooldown window, which is precisely when you most want
    the alert to get through.
    """
    state = _load_state()
    entry = state.get(key) or {}
    last = entry.get("last_sent", 0)
    suppressed = int(entry.get("suppressed", 0))

    if time.time() - last < cooldown:
        state[key] = {"last_sent": last, "suppressed": suppressed + 1}
        _save_state(state)
        return False, suppressed + 1

    return True, suppressed


def _record_sent(key: str) -> None:
    """Start the cooldown clock. Called only after a confirmed delivery."""
    state = _load_state()
    state[key] = {"last_sent": time.time(), "suppressed": 0}
    _save_state(state)


# ---------------------------------------------------------------------------
# systemd introspection
# ---------------------------------------------------------------------------
def _systemctl_show(unit: str, props: list[str]) -> dict:
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", ",".join(props)],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    result = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            result[k] = v
    return result


def _journal_tail(unit: str, lines: int = JOURNAL_LINES) -> str:
    """Last log lines for a unit, minus the polling noise."""
    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", unit, "-n", str(lines * 3),
             "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    keep = [ln for ln in out.splitlines()
            if ln.strip() and "HTTP Request" not in ln]
    return "\n".join(keep[-lines:])


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
def _enabled(config: dict) -> bool:
    return bool(config.get("liveness_alerts", True))


def _cooldown(config: dict) -> int:
    try:
        return int(config.get("liveness_cooldown_seconds", DEFAULT_COOLDOWN_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_COOLDOWN_SECONDS


def announce_up(service: str, detail: str = "") -> bool:
    """Called by a service once it is initialized and actually serving."""
    config = load_config()
    if not _enabled(config):
        return False

    key = f"up:{service}"
    send, suppressed = _check_cooldown(key, _cooldown(config))
    if not send:
        return False

    now = datetime.now().strftime("%-I:%M %p")
    msg = f"🟢 {service} is up ({now})"
    if detail:
        msg += f"\n{detail}"
    if suppressed:
        msg += f"\n\n({suppressed} earlier restart(s) not sent — within cooldown)"

    if send_message(msg, config):
        _record_sent(key)
        return True
    return False


AUTH_ALERT_COOLDOWN_SECONDS = 6 * 3600  # services retry every few seconds; text at most every 6h


def announce_auth_needed(label: str, reason: str = "") -> bool:
    """A Google login expired while a service was running with nobody at the keyboard."""
    config = load_config()
    if not _enabled(config):
        return False

    key = f"auth:{label}"
    send, _ = _check_cooldown(key, max(_cooldown(config), AUTH_ALERT_COOLDOWN_SECONDS))
    if not send:
        return False

    msg = (f"🟡 Google '{label}' needs you to sign in again"
           + (f" ({reason[:120]})" if reason else "")
           + f". Its calendar and email are off until then. On the computer run:\n"
             f"cd ~/Projects/secretary && .venv/bin/python src/google_auth.py {label}")
    if send_message(msg, config):
        _record_sent(key)
        return True
    return False


def announce_down(unit: str) -> bool:
    """Called by systemd OnFailure= when a unit enters the failed state."""
    config = load_config()
    if not _enabled(config):
        return False

    key = f"down:{unit}"
    send, suppressed = _check_cooldown(key, _cooldown(config))

    info = _systemctl_show(unit, ["Result", "ExecMainStatus", "ExecMainCode", "NRestarts"])
    result = info.get("Result", "unknown")
    status = info.get("ExecMainStatus", "?")
    restarts = info.get("NRestarts", "0")

    if not send:
        return False

    now = datetime.now().strftime("%-I:%M %p")
    lines = [f"🔴 {unit} FAILED ({now})",
             f"result={result} exit={status} restarts={restarts}"]
    if suppressed:
        lines.append(f"({suppressed} more failure(s) suppressed by cooldown)")

    tail = _journal_tail(unit)
    if tail:
        lines.append(f"\nLast log:\n{tail}")

    msg = "\n".join(lines)
    if len(msg) > 1800:  # keep it readable on a phone
        msg = msg[:1800] + "\n…(truncated)"

    if send_message(msg, config):
        _record_sent(key)
        return True
    return False


# ---------------------------------------------------------------------------
# CLI — used by the systemd OnFailure unit
# ---------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: liveness.py {alert|up|test} <unit-or-name>", file=sys.stderr)
        return 2

    command, target = argv[0], argv[1]

    if command == "alert":
        return 0 if announce_down(target) else 1
    if command == "up":
        return 0 if announce_up(target) else 1
    if command == "test":
        config = load_config()
        ok = send_message(f"🔎 Savvy liveness test ({target})", config)
        print("sent" if ok else "FAILED — is the signal-api container up?")
        return 0 if ok else 1

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

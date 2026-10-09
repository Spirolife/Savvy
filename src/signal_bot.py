#!/usr/bin/env python3
"""
Signal Bot — Two-way secretary via Signal.
Uses the signal-cli-rest-api container over HTTP (see notifier.py).

Run as a background service:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python signal_bot.py
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic

from core import respond, CALENDAR_AVAILABLE, EMAIL_AVAILABLE
from memory import Memory
from notifier import load_config, check_signal_cli, send_message, receive_messages
from liveness import announce_up
from diary import store_entry

try:
    from paths import CONFIG_PATH
except ImportError:
    CONFIG_PATH = Path(__file__).parent.parent / "credentials" / "config.json"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("secretary.signal_bot")


# ---------------------------------------------------------------------------
# Config & client
# ---------------------------------------------------------------------------
def load_app_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def get_client() -> anthropic.Anthropic:
    config = load_app_config()
    api_key = os.environ.get("ANTHROPIC_API_KEY") or config.get("anthropic_api_key")
    if not api_key:
        logger.error("No API key. Set ANTHROPIC_API_KEY or add to config.json")
        raise SystemExit(1)
    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------
# Context, prompt, memory and history all come from core.respond — the same
# backend the REPL uses. This file only owns the Signal transport and the
# one-line tool summaries shown on the phone.

# What identifies each tool call to a human reading it on their phone. Values are
# field names looked up in the call's input first, then in its JSON result, so
# id-only calls (delete_calendar_event, complete_task) can still name the thing
# they acted on — the dispatch in tools.py puts the title in the result for those.
_TOOL_SUMMARY_FIELDS = {
    # calendar
    "create_calendar_event":  ["summary", "start_time"],
    "create_allday_event":    ["summary", "date"],
    "quick_add_event":        ["text"],
    "update_calendar_event":  ["summary", "start_time"],
    "delete_calendar_event":  ["summary", "start"],
    "move_event":             ["summary", "dest_calendar"],
    "create_calendar":        ["name"],
    "delete_calendar":        ["name", "calendar_name"],
    "get_calendar_range":     ["start_date", "end_date"],
    "list_calendars":         [],
    # email
    "send_email":             ["to", "subject"],
    "draft_email":            ["to", "subject"],
    "send_draft":             ["subject", "draft_id"],
    "search_emails":          ["query"],
    "read_full_email":        ["subject", "from"],
    "read_thread":            ["subject"],
    "get_recent_emails":      [],
    "star_email":             ["subject"],
    "archive_email":          ["subject"],
    "mark_email_read":        ["subject"],
    "trash_email":            ["subject"],
    "list_drafts":            [],
    "return_email_labels":    [],
    "create_email_label":     ["name"],
    # tasks
    "create_task":            ["title", "task_list"],
    "update_task":            ["title", "task"],
    "complete_task":          ["task"],
    "reopen_task":            ["task"],
    "delete_task":            ["task"],
    "move_task":              ["task", "task_list"],
    "list_tasks":             ["task_list", "filter"],
    "list_task_lists":        [],
    "create_task_list":       ["name"],
    "return_task_labels":     [],
    "list_completed_tasks":   ["since", "until"],
    # diary, notes, misc
    "store_diary_entry":      ["entry_type"],
    "save_note":              ["filename"],
    "read_note":              ["filename"],
    "list_notes":             [],
    "remember_rule":          ["rule"],
    "save_fact":              ["fact"],
    "add_goal":               ["title", "target"],
    "list_goals":             ["status"],
    "update_goal":            ["title", "status"],
    "log_goal_progress":      ["goal", "note"],
    "add_meal":               ["main", "week"],
    "get_meal_plan":          ["week"],
    "update_meal":            ["main", "week"],
    "remove_meal":            ["removed"],
    "update_meal_settings":   ["reminder_day", "temporary_diet"],
    "log_bad_day":            ["date", "logged"],
    "check_rest_need":        [],
    "check_history":          ["query"],
    "schedule_reminder":      ["when", "instruction"],
    "list_reminders":         [],
}

# Never worth showing: opaque handles and routing details.
_NOISE_FIELDS = {
    "_importance", "account", "calendar_id", "event_id", "task_id", "message_id",
    "thread_id", "draft_id", "parent_id", "id", "source_calendar", "source_calendar_id",
    "dest_calendar_id", "success", "denied", "link", "description", "notes", "body",
}

_MAX_FIELD_LEN = 40


def _pretty_value(value) -> str:
    """Render one field: humanize datetimes, shorten anything long."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(_pretty_value(v) for v in value if v) [:_MAX_FIELD_LEN]
    text = str(value).strip()

    # ISO datetime -> "Sep 8 11:15 PM"; ISO date -> "Sep 8"
    iso = text.replace("Z", "+00:00")
    for parse, fmt in ((datetime.fromisoformat, "%b %-d %-I:%M %p"),):
        try:
            dt = parse(iso)
            return dt.strftime("%b %-d") if len(text) <= 10 else dt.strftime(fmt)
        except (ValueError, TypeError):
            pass

    text = " ".join(text.split())
    if len(text) > _MAX_FIELD_LEN:
        text = text[:_MAX_FIELD_LEN - 1].rstrip() + "…"
    return text


def _format_tool_event(name: str, inp: dict, result: str) -> str:
    """One terse line per tool call: `tool(what, when)`.

    Shows the thing acted on rather than the arguments used to find it — an
    event title and time, not an event id.
    """
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if not isinstance(parsed, dict):
        parsed = {}

    failed = parsed.get("success") is False or "error" in parsed or parsed.get("denied")
    status = "✗" if failed else "✓"

    fields = _TOOL_SUMMARY_FIELDS.get(name)
    if fields is None:
        # Unknown tool: show its least-noisy inputs rather than nothing.
        fields = [k for k in inp
                  if k not in _NOISE_FIELDS and not k.endswith("_id")][:2]

    parts = []
    for key in fields:
        raw = inp.get(key)
        if raw in (None, ""):
            raw = parsed.get(key)
        rendered = _pretty_value(raw)
        if rendered:
            parts.append(rendered)

    if not parts and failed:
        reason = parsed.get("error") or ("denied" if parsed.get("denied") else "")
        if reason:
            parts = [_pretty_value(reason)]

    return f"{status} {name}({', '.join(parts)})"


def process_message(
    client: anthropic.Anthropic,
    memory: Memory,
    message: str,
    config: dict,
) -> str:
    lower = message.strip().lower()

    # BOD/EOD shortcuts also land in the diary; the message itself still goes
    # through the normal turn below.
    if lower.startswith("bod:") or lower.startswith("morning plan:"):
        store_entry("bod", message.split(":", 1)[1].strip())
    elif lower.startswith("eod:") or lower.startswith("evening:"):
        store_entry("eod", message.split(":", 1)[1].strip())

    tool_events: list[str] = []
    try:
        reply, _, _ = respond(
            client, memory, message, surface="signal", config=config,
            tool_event_callback=lambda name, inp, result: tool_events.append(
                _format_tool_event(name, inp, result)
            ),
        )
        reply = reply or "(action completed)"
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        reply = "Sorry, I hit an error processing that. Try again in a moment."

    if tool_events:
        return "\n".join(tool_events) + "\n\n" + reply
    return reply


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_bot():
    config = load_config()
    app_config = load_app_config()
    memory = Memory(session_id="signal_bot")
    client = get_client()

    sender = config.get("sender_number", "")
    allowed_raw = config.get("recipient_number", "")
    allowed = [allowed_raw] if isinstance(allowed_raw, str) else list(allowed_raw or [])

    if not sender:
        logger.error("Set sender_number in config.json")
        sys.exit(1)

    # Verify signal-cli
    status = check_signal_cli(config)
    if not status["ok"]:
        logger.error(f"signal-cli not available: {status['error']}")
        sys.exit(1)
    logger.info(f"signal-cli: {status['version']}")

    # Verify Claude API
    model = app_config.get("anthropic_model", "claude-sonnet-4-6")
    try:
        client.messages.create(
            model=model, max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        logger.info(f"Claude API: connected ({model})")
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        sys.exit(1)

    logger.info("Signal Bot started")
    logger.info(f"  Listening as: {sender}")
    logger.info(f"  Responding to: {', '.join(allowed) if allowed else 'anyone'}")
    logger.info(f"  Calendar: {'yes' if CALENDAR_AVAILABLE else 'no'}")
    logger.info(f"  Email: {'yes' if EMAIL_AVAILABLE else 'no'}")

    # Drop facts past their hard expiry. Cheap, and running it at startup means
    # it happens on every restart without needing its own timer.
    try:
        expired = memory.purge_expired_facts()
        if expired:
            logger.info(f"Purged {len(expired)} expired fact(s) from memory")
    except Exception as e:
        logger.error(f"Fact purge failed: {e}")

    try:
        announce_up("Signal bot", detail=f"model {model} · calendar {'ok' if CALENDAR_AVAILABLE else 'off'} · email {'ok' if EMAIL_AVAILABLE else 'off'}")
    except Exception as e:
        logger.error(f"Startup notification failed: {e}")

    processed_timestamps = set()

    while True:
        try:
            messages = receive_messages(config)

            for msg in messages:
                source = msg["source"]
                text = msg["message"]
                ts = msg.get("timestamp", 0)

                if ts in processed_timestamps:
                    continue
                processed_timestamps.add(ts)

                if allowed and source not in allowed:
                    logger.info(f"Ignoring message from {source}")
                    continue

                logger.info(f"Received: {text[:100]}")

                reply = process_message(client, memory, text, app_config)

                logger.info(f"Replying: {reply[:100]}")
                send_message(reply, config)

            if len(processed_timestamps) > 1000:
                processed_timestamps = set(sorted(processed_timestamps)[-500:])

        except Exception as e:
            logger.error(f"Bot error: {e}", exc_info=True)

        time.sleep(5)


if __name__ == "__main__":
    print("=" * 50)
    print("  Secretary Signal Bot")
    print("  Text your secretary from your phone")
    print("  Ctrl+C to stop")
    print("=" * 50)
    print()
    try:
        run_bot()
    except KeyboardInterrupt:
        print("\nBot stopped.")
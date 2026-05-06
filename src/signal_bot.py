#!/usr/bin/env python3
"""
Signal Bot — Two-way secretary via Signal.
Uses signal-cli native binary directly — no container needed.

Run as a background service:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python signal_bot.py
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import anthropic

from prompt import SIGNAL_SYSTEM_PROMPT as SYSTEM_PROMPT, FACT_EXTRACTION_PROMPT

from memory import Memory
from notifier import load_config, check_signal_cli, send_message, receive_messages
from tools import chat_with_tools
from diary import (
    store_entry, get_today_entries, get_recent_entries,
    format_entries_for_context,
)

try:
    from paths import CONFIG_PATH
except ImportError:
    CONFIG_PATH = Path(__file__).parent.parent / "credentials" / "config.json"

# Import integrations gracefully
try:
    from calendar_integration import (
        get_today_events, get_upcoming_events, get_current_event,
        format_events_for_context,
    )
    CALENDAR_AVAILABLE = True
except Exception:
    CALENDAR_AVAILABLE = False

try:
    from email_integration import (
        get_unread_count, get_important_unread, format_emails_for_context,
    )
    EMAIL_AVAILABLE = True
except Exception:
    EMAIL_AVAILABLE = False


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
# Context gathering
# ---------------------------------------------------------------------------
def gather_context(memory: Memory) -> str:
    parts = []
    now = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    parts.append(f"Current time: {now}")

    if CALENDAR_AVAILABLE:
        try:
            today = get_today_events()
            if today:
                parts.append("TODAY'S CALENDAR:\n" + format_events_for_context(today))
            upcoming = get_upcoming_events(hours_ahead=4)
            if upcoming:
                parts.append("NEXT 4 HOURS:\n" + format_events_for_context(upcoming))
            current = get_current_event()
            if current:
                parts.append(f"CURRENTLY IN: {current['summary']}")
        except Exception:
            pass

    if EMAIL_AVAILABLE:
        try:
            counts = get_unread_count()
            unread_str = ", ".join(f"{l}: {n}" for l, n in counts.items())
            parts.append(f"UNREAD EMAILS: {unread_str}")
            important = get_important_unread(max_results=3)
            if important:
                parts.append("IMPORTANT UNREAD:\n" + format_emails_for_context(important))
        except Exception:
            pass

    today_diary = get_today_entries()
    if today_diary:
        parts.append("TODAY'S DIARY:\n" + format_entries_for_context(today_diary))

    facts = memory.retrieve_facts("goals tasks deadlines schedule", top_k=10)
    if facts:
        parts.append("KNOWN FACTS & GOALS:\n" + "\n".join(f"- {f}" for f in facts))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------

signal_conversation: list[dict] = []
MAX_SIGNAL_HISTORY = 10


def process_message(
    client: anthropic.Anthropic,
    memory: Memory,
    message: str,
    config: dict,
) -> str:
    model = config.get("anthropic_model", "claude-sonnet-4-6")

    lower = message.strip().lower()

    # BOD/EOD shortcuts
    if lower.startswith("bod:") or lower.startswith("morning plan:"):
        content = message.split(":", 1)[1].strip()
        store_entry("bod", content)
        memory.store("user", f"[Morning Plan via Signal] {content}")

    elif lower.startswith("eod:") or lower.startswith("evening:"):
        content = message.split(":", 1)[1].strip()
        store_entry("eod", content)
        memory.store("user", f"[Evening Reflection via Signal] {content}")

    context = gather_context(memory)
    system = SYSTEM_PROMPT.format(context=context)

    relevant = memory.retrieve_relevant(message, top_k=5)
    if relevant:
        memory_lines = []
        for msg in relevant:
            ts = datetime.fromtimestamp(msg["timestamp"]).strftime("%b %d %I:%M %p")
            memory_lines.append(f"[{ts}] {msg['role']}: {msg['content'][:200]}")
        system += "\n\nRELEVANT PAST CONVERSATIONS:\n" + "\n".join(memory_lines)

    messages = list(signal_conversation[-MAX_SIGNAL_HISTORY:])
    messages.append({"role": "user", "content": message})

    try:
        reply, _, _ = chat_with_tools(
            client, model, system, messages,
            max_tokens=,
        )
        reply = reply.strip()
        if not reply:
            reply = "(action completed)"
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        reply = "Sorry, I hit an error processing that. Try again in a moment."

    memory.store("user", f"[Signal] {message}")
    memory.store("assistant", f"[Signal] {reply}")

    signal_conversation.append({"role": "user", "content": message})
    signal_conversation.append({"role": "assistant", "content": reply})

    while len(signal_conversation) > MAX_SIGNAL_HISTORY * 2:
        signal_conversation.pop(0)

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
    allowed = config.get("recipient_number", "")

    if not sender:
        logger.error("Set sender_number in config.json")
        return

    # Verify signal-cli
    status = check_signal_cli(config)
    if not status["ok"]:
        logger.error(f"signal-cli not available: {status['error']}")
        return
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
        return

    logger.info("Signal Bot started")
    logger.info(f"  Listening as: {sender}")
    logger.info(f"  Responding to: {allowed or 'anyone'}")
    logger.info(f"  Calendar: {'yes' if CALENDAR_AVAILABLE else 'no'}")
    logger.info(f"  Email: {'yes' if EMAIL_AVAILABLE else 'no'}")

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

                if allowed and source != allowed:
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
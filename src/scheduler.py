#!/usr/bin/env python3
"""
Background scheduler for the private secretary.
Handles diary prompts, smart check-ins, and calendar-aware recommendations.

Uses Claude (Anthropic API) for reasoning about when/what to notify.
Sends notifications via local Signal container.

Run as a background service:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python scheduler.py
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import anthropic

from memory import Memory
from notifier import load_config as load_signal_config, send_notification, check_signal_api
from diary import (
    has_entry_today, get_today_entries, get_recent_entries,
    get_weekly_summary_data, format_entries_for_context,
)

# Import integrations gracefully (may not be set up yet)
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
logger = logging.getLogger("secretary.scheduler")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
from paths import CONFIG_PATH, SCHEDULER_STATE_PATH as STATE_PATH


def load_config() -> dict:
    """Load merged config (app config + signal config)."""
    config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    return config


def get_client() -> anthropic.Anthropic:
    config = load_config()
    api_key = os.environ.get("ANTHROPIC_API_KEY") or config.get("anthropic_api_key")
    if not api_key:
        logger.error("No API key. Set ANTHROPIC_API_KEY or add to config.json")
        raise SystemExit(1)
    return anthropic.Anthropic(api_key=api_key)


MODEL = load_config().get("anthropic_model", "claude-sonnet-4-6")


def ask_llm(client: anthropic.Anthropic, prompt: str, max_tokens: int = 512) -> str:
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.error(f"API request failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Notification guards
# ---------------------------------------------------------------------------
def is_quiet_hours(config: dict) -> bool:
    hour = datetime.now().hour
    start = config.get("quiet_hours_start", 23)
    end = config.get("quiet_hours_end", 7)
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end


def can_notify(state: dict, config: dict) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_notification_date") != today:
        state["notifications_today"] = 0
        state["last_notification_date"] = today

    max_per_day = config.get("max_notifications_per_day", 8)
    if state.get("notifications_today", 0) >= max_per_day:
        return False
    if is_quiet_hours(config):
        return False
    return True


def do_send(title: str, body: str, state: dict, config: dict) -> bool:
    if not can_notify(state, config):
        return False
    ok = send_notification(title, body, config)
    if ok:
        state["notifications_today"] = state.get("notifications_today", 0) + 1
        save_state(state)
    return ok


def is_within_window(target_time: str, window_minutes: int = 10) -> bool:
    """Check if current time is within N minutes of a target HH:MM."""
    try:
        now = datetime.now()
        hour, minute = map(int, target_time.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        diff = abs((now - target).total_seconds())
        return diff < window_minutes * 60
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------
def gather_context(memory: Memory) -> dict:
    """Gather all available context for the LLM."""
    ctx = {
        "now": datetime.now().strftime("%A, %B %d, %Y at %I:%M %p"),
        "today_events": "",
        "upcoming_events": "",
        "current_event": None,
        "unread_email_count": "",
        "important_emails": "",
        "diary_today": "",
        "diary_recent": "",
        "recent_conversations": "",
        "known_facts": "",
    }

    # Calendar
    if CALENDAR_AVAILABLE:
        try:
            today = get_today_events()
            ctx["today_events"] = format_events_for_context(today)
            upcoming = get_upcoming_events(hours_ahead=4)
            ctx["upcoming_events"] = format_events_for_context(upcoming)
            ctx["current_event"] = get_current_event()
        except Exception as e:
            logger.debug(f"Calendar error: {e}")

    # Email
    if EMAIL_AVAILABLE:
        try:
            counts = get_unread_count()
            ctx["unread_email_count"] = ", ".join(f"{l}: {n}" for l, n in counts.items())
            important = get_important_unread(max_results=3)
            ctx["important_emails"] = format_emails_for_context(important)
        except Exception as e:
            logger.debug(f"Email error: {e}")

    # Diary
    today_diary = get_today_entries()
    ctx["diary_today"] = format_entries_for_context(today_diary)
    recent_diary = get_recent_entries(days=3)
    ctx["diary_recent"] = format_entries_for_context(recent_diary)

    # Memory
    recent = memory.retrieve_recent(n=15)
    if recent:
        lines = []
        for msg in recent:
            ts = datetime.fromtimestamp(msg["timestamp"]).strftime("%b %d %I:%M %p")
            lines.append(f"[{ts}] {msg['role']}: {msg['content'][:200]}")
        ctx["recent_conversations"] = "\n".join(lines[-10:])

    facts = memory.retrieve_facts("goals deadlines tasks projects resolutions", top_k=15)
    if facts:
        ctx["known_facts"] = "\n".join(f"- {f}" for f in facts)

    return ctx


# ---------------------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------------------
def bod_prompt(state: dict, config: dict):
    """Send morning diary prompt."""
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_bod_prompt") == today:
        return

    if has_entry_today("bod"):
        state["last_bod_prompt"] = today
        save_state(state)
        return

    logger.info("Sending BOD diary prompt")

    # Include today's calendar for context
    cal_context = ""
    if CALENDAR_AVAILABLE:
        try:
            events = get_today_events()
            if events:
                cal_context = f"\n\nYour calendar for today:\n{format_events_for_context(events)}"
        except Exception:
            pass

    message = (
        f"Good morning! What's your plan for today?{cal_context}\n\n"
        "Open the secretary and type /bod to log your morning plan."
    )

    do_send("Morning Check-in", message, state, config)
    state["last_bod_prompt"] = today
    save_state(state)


def eod_prompt(state: dict, config: dict):
    """Send evening diary prompt."""
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_eod_prompt") == today:
        return

    if has_entry_today("eod"):
        state["last_eod_prompt"] = today
        save_state(state)
        return

    logger.info("Sending EOD diary prompt")

    # Include today's BOD for reflection
    today_entries = get_today_entries()
    bod_context = ""
    for e in today_entries:
        if e["type"] == "bod":
            bod_context = f"\n\nThis morning you planned:\n{e['content'][:300]}"
            break

    message = (
        f"How did today go?{bod_context}\n\n"
        "Open the secretary and type /eod to log your evening reflection."
    )

    do_send("Evening Reflection", message, state, config)
    state["last_eod_prompt"] = today
    save_state(state)


def smart_checkin(client: anthropic.Anthropic, memory: Memory, state: dict, config: dict, checkin_label: str):
    """
    Smart check-in that decides whether to notify based on context.
    
    Key behavior: if the LLM determines there's nothing worth notifying about,
    it responds with NONE and no Signal message is sent.
    
    If there's a vague calendar block (e.g. "work on personal project"),
    the LLM looks at goals/NYRs/project log and recommends specifics.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    checkin_key = f"last_checkin_{checkin_label}_{today}"
    if state.get(checkin_key):
        return

    logger.info(f"Running smart check-in: {checkin_label}")
    ctx = gather_context(memory)

    prompt = f"""You are a private secretary. Current time: {ctx['now']}

TODAY'S CALENDAR:
{ctx['today_events']}

NEXT 4 HOURS:
{ctx['upcoming_events']}

{"CURRENTLY IN: " + ctx['current_event']['summary'] if ctx['current_event'] else ""}

UNREAD EMAILS: {ctx['unread_email_count']}
{("IMPORTANT UNREAD:" + chr(10) + ctx['important_emails']) if ctx['important_emails'] and ctx['important_emails'] != '(no emails)' else ""}

TODAY'S DIARY:
{ctx['diary_today']}

RECENT DIARY (last 3 days):
{ctx['diary_recent']}

RECENT CONVERSATIONS:
{ctx['recent_conversations']}

KNOWN FACTS, GOALS, AND COMMITMENTS:
{ctx['known_facts']}

---

You are doing a {checkin_label} check-in. Evaluate whether there is anything \
worth notifying the user about RIGHT NOW. Consider:

1. UPCOMING EVENTS: Is there something in the next 1-2 hours they should prepare for?

2. VAGUE CALENDAR BLOCKS: If there's a block like "personal project time", \
"work on research", "free time", or any non-specific event coming up, look at \
their goals, new year's resolutions, and project log. Recommend a SPECIFIC \
thing to work on and why (based on deadlines, momentum, or what they haven't \
touched in a while).

3. IMPORTANT EMAILS: Anything urgent or from someone important they haven't \
seen?

4. FORGOTTEN TASKS: Anything from their diary or conversations that seems \
dropped?

5. ENCOURAGEMENT: If they've been making good progress on something, a brief \
acknowledgment is welcome.

RULES:
- If there IS something worth saying, write a concise notification (2-4 sentences).
  Be specific and actionable. Don't be generic.
- If there is genuinely NOTHING worth notifying about right now, respond with \
  exactly the word NONE and nothing else. Do not force a notification.
- Never be annoying. Quality over quantity.
- Match the tone to the time of day: morning = energetic, midday = focused, \
  afternoon = winding down."""

    response = ask_llm(client, prompt, max_tokens=300)
    logger.info(f"Check-in [{checkin_label}]: {response[:200]}")

    if response and response.strip().upper() != "NONE":
        do_send(f"Check-in", response, state, config)

    state[checkin_key] = True
    save_state(state)


def weekly_review(client: anthropic.Anthropic, memory: Memory, state: dict, config: dict):
    """Sunday evening weekly review."""
    today = datetime.now()
    review_day = config.get("weekly_review_day", "Sunday")
    if today.strftime("%A") != review_day:
        return

    week_key = f"weekly_review_{today.strftime('%Y-W%W')}"
    if state.get(week_key):
        return

    review_time = config.get("weekly_review_time", "18:00")
    if not is_within_window(review_time, window_minutes=15):
        return

    logger.info("Running weekly review")
    weekly_data = get_weekly_summary_data()
    ctx = gather_context(memory)

    bod_summaries = "\n".join(
        f"  {e['date']}: {e['content'][:150]}" for e in weekly_data["bod_entries"]
    ) or "  (none)"
    eod_summaries = "\n".join(
        f"  {e['date']}: {e['content'][:150]}" for e in weekly_data["eod_entries"]
    ) or "  (none)"

    prompt = f"""You are a private secretary doing a WEEKLY REVIEW. Today: {ctx['now']}

MORNING PLANS THIS WEEK ({weekly_data['days_with_bod']}/7 days logged):
{bod_summaries}

EVENING REFLECTIONS THIS WEEK ({weekly_data['days_with_eod']}/7 days logged):
{eod_summaries}

KNOWN GOALS AND COMMITMENTS:
{ctx['known_facts']}

UPCOMING WEEK CALENDAR:
{ctx['today_events']}

Write a thoughtful weekly review (5-8 sentences) that:
1. Summarizes what they accomplished vs planned
2. Notes patterns (what went well, what kept slipping)
3. Highlights goals they made progress on and ones that need attention
4. Suggests 2-3 specific priorities for next week
5. Gives honest but encouraging feedback

Be specific — reference their actual entries and goals, not generic advice."""

    response = ask_llm(client, prompt, max_tokens=600)
    if response:
        do_send("Weekly Review", response, state, config)
        state[week_key] = True
        save_state(state)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_scheduler():
    config = load_config()
    state = load_state()
    memory = Memory(session_id="scheduler")
    client = get_client()

    # Verify Signal
    signal_status = check_signal_api(config)
    if not signal_status["ok"]:
        logger.error(f"Signal API not available: {signal_status['error']}")
        return

    if not config.get("sender_number") or not config.get("recipient_number"):
        logger.error("Configure sender_number and recipient_number in config.json")
        return

    # Verify Anthropic
    try:
        client.messages.create(
            model=MODEL, max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        logger.info(f"Anthropic API: connected ({MODEL})")
    except Exception as e:
        logger.error(f"Anthropic API error: {e}")
        return

    logger.info("Scheduler started")
    logger.info(f"  BOD prompt:    {config.get('bod_time', '08:30')}")
    logger.info(f"  EOD prompt:    {config.get('eod_time', '22:00')}")
    logger.info(f"  Check-ins:     {config.get('checkin_times', ['09:30', '13:00', '16:30'])}")
    logger.info(f"  Weekly review: {config.get('weekly_review_day', 'Sunday')} {config.get('weekly_review_time', '18:00')}")
    logger.info(f"  Calendar:      {'connected' if CALENDAR_AVAILABLE else 'not set up'}")
    logger.info(f"  Email:         {'connected' if EMAIL_AVAILABLE else 'not set up'}")
    logger.info(f"  Max notif/day: {config.get('max_notifications_per_day', 8)}")
    logger.info(f"  Quiet hours:   {config.get('quiet_hours_start', 23)}:00-{config.get('quiet_hours_end', 7)}:00")

    while True:
        try:
            config = load_config()
            state = load_state()

            # Reset daily counters
            today = datetime.now().strftime("%Y-%m-%d")
            if state.get("last_notification_date") != today:
                state["notifications_today"] = 0
                state["last_notification_date"] = today
                save_state(state)

            # --- BOD prompt ---
            bod_time = config.get("bod_time", "08:30")
            if is_within_window(bod_time):
                bod_prompt(state, config)

            # --- EOD prompt ---
            eod_time = config.get("eod_time", "22:00")
            if is_within_window(eod_time):
                eod_prompt(state, config)

            # --- Smart check-ins ---
            checkin_times = config.get("checkin_times", ["09:30", "13:00", "16:30"])
            labels = ["morning", "midday", "afternoon"]
            for i, ct in enumerate(checkin_times):
                label = labels[i] if i < len(labels) else f"checkin_{i}"
                if is_within_window(ct):
                    smart_checkin(client, memory, state, config, label)

            # --- Weekly review ---
            weekly_review(client, memory, state, config)

        except Exception as e:
            logger.error(f"Scheduler error: {e}", exc_info=True)

        # Sleep 3 minutes between cycles
        time.sleep(180)


if __name__ == "__main__":
    print("=" * 50)
    print("  Secretary Scheduler")
    print("  Calendar-aware • Diary prompts • Smart check-ins")
    print("  Ctrl+C to stop")
    print("=" * 50)
    print()
    try:
        run_scheduler()
    except KeyboardInterrupt:
        print("\nScheduler stopped.")
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
from reminders import pop_due_reminders, requeue
from tools import chat_with_tools, read_only_tools, request_options
from core import build_context, SURFACE_TAG
from prompt import SYSTEM_PROMPT, CHECKIN_INSTRUCTION
import meals
import rest

# Import integrations gracefully (may not be set up yet)
try:
    from calendar_integration import (
        get_today_events, get_next_week_events, format_events_for_context,
        format_day_for_user,
    )
    CALENDAR_AVAILABLE = True
except Exception:
    CALENDAR_AVAILABLE = False

try:
    import email_integration  # noqa: F401  (availability flag only)
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


def do_send(title: str, body: str, state: dict, config: dict, memory: Memory | None = None) -> bool:
    if not can_notify(state, config):
        return False
    ok = send_notification(title, body, config)
    if ok:
        state["notifications_today"] = state.get("notifications_today", 0) + 1
        save_state(state)
        # Record it as something Savvy said on Signal, so when the user texts
        # back "yes, I did" the next turn knows what they're answering.
        if memory is not None:
            memory.store("assistant", SURFACE_TAG["signal"] + body)
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
# Autonomous turns — same prompt and context as a user message
# ---------------------------------------------------------------------------
def autonomous_turn(client: anthropic.Anthropic, memory: Memory, instruction: str,
                    read_only: bool) -> str:
    """Run `instruction` through the normal backend with nobody present.

    Same system prompt, rules, goals, facts, calendar and history as a message
    from the user (core.build_context), styled for Signal. read_only limits it to
    level-0 tools, for runs that should report rather than act.
    """
    config = load_config()
    options, max_tokens = request_options(config, MODEL, config.get("signal_max_tokens", 2000))
    system, messages = build_context(instruction, memory, SYSTEM_PROMPT, surface="signal")
    reply, _, _ = chat_with_tools(
        client, MODEL, system, messages, max_tokens=max_tokens,
        tools=read_only_tools() if read_only else None, options=options,
    )
    return reply.strip()


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
                cal_context = f"\n\nToday:\n{format_day_for_user(events)}"
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
    """Check-in that decides whether to message at all.

    Runs through the full backend with read-only tools, so it can look at the
    calendar, tasks and email for itself, and asks for updates on whatever the
    user planned but hasn't reported. If nothing is worth saying the model
    answers NONE and nothing is sent.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    checkin_key = f"last_checkin_{checkin_label}_{today}"
    if state.get(checkin_key):
        return

    logger.info(f"Running smart check-in: {checkin_label}")
    try:
        response = autonomous_turn(client, memory, CHECKIN_INSTRUCTION.format(label=checkin_label),
                                   read_only=True)
    except Exception as e:
        logger.error(f"Check-in [{checkin_label}] failed: {e}")
        return
    logger.info(f"Check-in [{checkin_label}]: {response[:200]}")

    if response and response.strip().upper() != "NONE":
        do_send("Check-in", response, state, config, memory)

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

    bod_summaries = "\n".join(
        f"  {e['date']}: {e['content'][:150]}" for e in weekly_data["bod_entries"]
    ) or "  (none)"
    eod_summaries = "\n".join(
        f"  {e['date']}: {e['content'][:150]}" for e in weekly_data["eod_entries"]
    ) or "  (none)"
    next_week = "(calendar unavailable)"
    if CALENDAR_AVAILABLE:
        try:
            next_week = format_events_for_context(get_next_week_events())
        except Exception as e:
            logger.error(f"Weekly review calendar fetch failed: {e}")

    instruction = f"""[Scheduled weekly review — I'm not in this conversation; whatever you write is texted to me.]

MORNING PLANS THIS WEEK ({weekly_data['days_with_bod']}/7 days logged):
{bod_summaries}

EVENING REFLECTIONS THIS WEEK ({weekly_data['days_with_eod']}/7 days logged):
{eod_summaries}

NEXT WEEK'S CALENDAR:
{next_week}

Write a thoughtful weekly review (5-8 sentences). Check completed tasks with your tools first.
1. What I accomplished vs planned
2. Patterns: what went well, what kept slipping
3. Goals I made progress on and ones that need attention
4. 2-3 specific priorities for next week, from the calendar above and my tasks
5. Honest but encouraging feedback
Reference my actual entries and goals, not generic advice."""

    try:
        response = autonomous_turn(client, memory, instruction, read_only=True)
    except Exception as e:
        logger.error(f"Weekly review failed: {e}")
        return
    if response:
        do_send("Weekly Review", response, state, config, memory)
        state[week_key] = True
        save_state(state)


def meal_reminder(memory: Memory, state: dict, config: dict):
    """The weekly "here are your meals" message, on meals.reminder_day at reminder_time.

    Built straight from the plan — no model call — and saved to memory like any
    other Signal message, so a reply like "move the salmon to Thursday" has the
    context it's answering.
    """
    settings = meals.get_settings()
    if datetime.now().strftime("%A") != settings["reminder_day"]:
        return
    if not is_within_window(settings["reminder_time"], window_minutes=15):
        return
    week = meals.reminder_week()
    key = f"meal_reminder_{week}"
    if state.get(key):
        return

    items = [m for m in meals.get_week(week) if m["status"] != "skipped"]
    n_meals = sum(m["kind"] == "meal" for m in items)
    n_drinks = sum(m["kind"] == "drink" for m in items)
    label = datetime.strptime(week, "%Y-%m-%d").strftime("%b %-d")
    if items:
        lines = [f"Meals for the week of {label}:"]
        for m in items:
            line = f"- {m['main']}" + (" + " + " + ".join(m["sides"]) if m["sides"] else "")
            line += " (drink)" if m["kind"] == "drink" else ""
            line += f" — {m['day']}" if m.get("day") else ""
            lines.append(line)
        room = []
        if n_meals < settings["max_meals"] and n_meals + n_drinks < settings["max_total"]:
            room.append("another meal")
        if n_drinks < settings["max_drinks"] and n_meals + n_drinks < settings["max_total"]:
            room.append("a drink")
        lines.append(f"\n{n_meals} meal(s), {n_drinks} drink(s)"
                     + (f" — room for {' or '.join(room)}." if room else " — that's a full week."))
        lines.append("Want to move anything around?")
    else:
        ideas = len(meals.get_week(None))
        lines = [f"Nothing planned for the week of {label} yet"
                 + (f" — you have {ideas} recipe idea(s) saved." if ideas else ".")
                 + " Want me to slot some in?"]
    diet = meals.active_diet()
    if diet:
        lines.append(f"(Still on {diet}.)")

    logger.info(f"Sending meal reminder for week of {week}")
    if do_send("Meal plan", "\n".join(lines), state, config, memory):
        state[key] = True
        save_state(state)


def fact_check(memory: Memory, state: dict, config: dict):
    """Weekly "are these still true?" text for medium-term facts.

    Deterministic, like meal_reminder, and stored as a Signal message so the
    reply is answered in context: the system prompt says to refresh what the
    user confirms (save_fact, same wording) and forget_fact the rest.
    """
    if datetime.now().strftime("%A") != config.get("fact_check_day", "Saturday"):
        return
    if not is_within_window(config.get("fact_check_time", "12:00"), window_minutes=15):
        return
    key = f"fact_check_{datetime.now():%Y-%m-%d}"
    if state.get(key):
        return
    state[key] = True
    save_state(state)

    facts = memory.facts_to_check(limit=config.get("fact_check_count", 3))
    if not facts:
        return
    lines = ["Memory check: are these still true?"]
    lines += [f"{n}. {f['fact']} (noted {f['age']})" for n, f in enumerate(facts, 1)]
    lines.append("\nTell me which still hold and which don't. Anything you don't answer just fades over time.")
    logger.info(f"Fact check: asking about {len(facts)} fact(s)")
    if do_send("Memory check", "\n".join(lines), state, config, memory):
        memory.mark_checked([f["id"] for f in facts])


REST_INSTRUCTION = """[Scheduled rest-day check — I'm not in this conversation; whatever you write is texted to me.]

A rest day looks worth suggesting:
{assessment}

Write 2-4 sentences, plain text: say briefly why (the logged bad days, or the packed stretch), \
suggest the rest-day candidate above (or lightening a day if there's none), and what it could look \
like — a stay-home day with chores, low-key errands, and one or two specific hobbies or small \
projects from my Todoist Personal Projects / Fun lists (look them up). A rest day is not "do \
nothing". Offer to block it on the calendar; don't book anything. No guilt, no lecture."""


def rest_day_check(client: anthropic.Anthropic, memory: Memory, state: dict, config: dict):
    """Crash-out early warning: text once per trigger, at rest_days.check_time."""
    s = rest.settings(config)
    if not is_within_window(s["check_time"], window_minutes=15):
        return
    today_key = f"rest_checked_{datetime.now():%Y-%m-%d}"
    if state.get(today_key):
        return
    state[today_key] = True
    save_state(state)

    a = rest.assess(config=config)
    if not a["trigger"]:
        return
    # One message per trigger: the same bad days, or the same packed stretch,
    # don't re-text every evening.
    anchor = (",".join(sorted({b["date"] for b in a["bad_days"]})) if a["trigger"] == "bad_days"
              else a["packed_stretch"][0]["date"])
    key = f"rest_warning_{a['trigger']}_{anchor}"
    if state.get(key):
        return
    logger.info(f"Rest-day warning: {a['trigger']} ({anchor})")
    try:
        msg = autonomous_turn(client, memory, REST_INSTRUCTION.format(assessment=rest.format_assessment(a)),
                              read_only=True)
    except Exception as e:
        logger.error(f"Rest-day check failed: {e}")
        return
    if msg and msg.strip().upper() != "NONE" and do_send("Rest day", msg, state, config, memory):
        state[key] = True
        save_state(state)


def run_due_reminders(client: anthropic.Anthropic, memory: Memory, state: dict, config: dict):
    """Run any one-off scheduled reminders (from the schedule_reminder tool)
    that have come due, through the full tool-calling loop — not just a
    canned message — so conditional instructions can check real data and
    take real actions before notifying the user."""
    for reminder in pop_due_reminders():
        logger.info(f"Running scheduled reminder [{reminder['id']}]: {reminder['instruction'][:100]}")
        # Full tools: the user asked for this to act ("if I haven't done X, move Y").
        # Level-2 calls still need a human, and there isn't one, so they're denied.
        instruction = (
            "[Scheduled reminder I set up earlier — I'm not in this conversation, so don't ask me "
            "questions; check what needs checking with your tools, take any real action it "
            "describes, then write a 2-4 sentence plain-text message telling me what you found "
            "and did. It will be texted to me.]\n\n"
            f"Instruction: {reminder['instruction']}"
        )
        try:
            reply = autonomous_turn(client, memory, instruction, read_only=False)
            reply = reply or "(scheduled check-in ran but produced no message)"
        except Exception as e:
            logger.error(f"Scheduled reminder [{reminder['id']}] failed: {e}")
            reply = f"A scheduled check-in ({reminder['instruction'][:80]}...) failed to run: {e}"

        if do_send("Scheduled Check-in", reply, state, config, memory):
            logger.info(f"Scheduled reminder [{reminder['id']}] sent")
        else:
            logger.info(f"Scheduled reminder [{reminder['id']}] couldn't send (quiet hours/budget) — retrying next cycle")
            requeue(reminder)


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

            # --- Weekly meal plan ---
            meal_reminder(memory, state, config)

            # --- Weekly "still true?" check on medium-term facts ---
            fact_check(memory, state, config)

            # --- Crash-out early warning ---
            rest_day_check(client, memory, state, config)

            # --- One-off scheduled reminders ---
            run_due_reminders(client, memory, state, config)

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
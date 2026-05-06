"""
Core module — shared logic for both REPL and Signal bot modes.
Context gathering, config loading, tool-use chat, fact extraction.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import anthropic

from memory import Memory
from prompt import FACT_EXTRACTION_PROMPT

try:
    from paths import CONFIG_PATH
except ImportError:
    CONFIG_PATH = Path(__file__).parent.parent / "credentials" / "config.json"

# Import integrations gracefully
try:
    from calendar_integration import (
        get_today_events, get_upcoming_events, get_current_event,
        get_week_events, format_events_for_context,
    )
    CALENDAR_AVAILABLE = True
except Exception:
    CALENDAR_AVAILABLE = False

try:
    from email_integration import (
        get_unread_count, get_important_unread, get_recent_emails,
        format_emails_for_context,
    )
    EMAIL_AVAILABLE = True
except Exception:
    EMAIL_AVAILABLE = False

from diary import get_today_entries, get_recent_entries, format_entries_for_context
from tools import chat_with_tools


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_app_config() -> dict:
    config_path = str(CONFIG_PATH) if hasattr(CONFIG_PATH, 'exists') else CONFIG_PATH
    if os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)
    return {}


def get_client(config: dict | None = None) -> anthropic.Anthropic:
    config = config or load_app_config()
    api_key = os.environ.get("ANTHROPIC_API_KEY") or config.get("anthropic_api_key")
    if not api_key:
        raise ValueError("No API key. Set ANTHROPIC_API_KEY env var or add 'anthropic_api_key' to config.json")
    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------
def get_live_context() -> dict:
    """Gather calendar, email, and diary context. All runs locally."""
    ctx = {"calendar": "", "email": "", "diary": ""}

    if CALENDAR_AVAILABLE:
        try:
            week = get_week_events()
            if week:
                ctx["calendar"] = "THIS WEEK'S CALENDAR:\n" + format_events_for_context(week)
            current = get_current_event()
            if current:
                ctx["calendar"] += f"\n\nRIGHT NOW: {current['summary']}"
        except Exception:
            pass

    if EMAIL_AVAILABLE:
        try:
            unread_counts = get_unread_count()
            important = get_important_unread(max_results=3)
            unread_str = ", ".join(f"{label}: {n}" for label, n in unread_counts.items())
            parts = [f"UNREAD EMAILS: {unread_str}"]
            if important:
                parts.append("IMPORTANT UNREAD:\n" + format_emails_for_context(important))
            ctx["email"] = "\n".join(parts)
        except Exception:
            pass

    today_diary = get_today_entries()
    recent_diary = get_recent_entries(days=3)
    if today_diary or recent_diary:
        parts = []
        if today_diary:
            parts.append("TODAY'S DIARY:\n" + format_entries_for_context(today_diary))
        if recent_diary:
            parts.append("RECENT DIARY:\n" + format_entries_for_context(recent_diary))
        ctx["diary"] = "\n\n".join(parts)

    return ctx


def build_context(user_input: str, memory: Memory, system_template: str) -> tuple[str, list[dict]]:
    """Build system prompt and message list with all context.
    
    Args:
        user_input: Current user message
        memory: Memory instance
        system_template: System prompt template with {datetime}, {calendar_context}, etc.
    
    Returns:
        (system_prompt, messages)
    """
    config = load_app_config()
    context_top_k = config.get("context_top_k", 8)
    recent_window = config.get("recent_window", 10)

    live = get_live_context()

    system = system_template.format(
        datetime=datetime.now().strftime("%A, %B %d, %Y at %I:%M %p"),
        calendar_context=live["calendar"],
        email_context=live["email"],
        diary_context=live["diary"],
    )

    # Semantic memory search (local embeddings)
    relevant = memory.retrieve_relevant(user_input, top_k=context_top_k)
    relevant_facts = memory.retrieve_facts(user_input, top_k=8)

    memory_block = ""
    if relevant:
        memory_lines = []
        for msg in relevant:
            ts = datetime.fromtimestamp(msg["timestamp"]).strftime("%b %d, %I:%M %p")
            memory_lines.append(f"[{ts}] {msg['role']}: {msg['content'][:400]}")
        memory_block += "Relevant past conversations:\n" + "\n".join(memory_lines)

    if relevant_facts:
        memory_block += "\n\nKnown facts:\n" + "\n".join(f"- {f}" for f in relevant_facts)

    if memory_block:
        system += f"\n\n<past_context>\n{memory_block}\n</past_context>"

    # Recent sliding window
    messages = []
    recent = memory.retrieve_recent(n=recent_window)
    for msg in recent:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_input})

    return system, messages


# ---------------------------------------------------------------------------
# Fact extraction
# ---------------------------------------------------------------------------
def extract_facts(client: anthropic.Anthropic, model: str, user_msg: str, assistant_msg: str) -> list[str]:
    """Use Claude to extract structured facts from a conversation turn."""
    try:
        prompt = FACT_EXTRACTION_PROMPT.format(
            user_message=user_msg, assistant_message=assistant_msg
        )
        resp = client.messages.create(
            model=model, max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        facts = json.loads(text)
        if isinstance(facts, list):
            return [str(f) for f in facts if f]
        return []
    except Exception:
        return []
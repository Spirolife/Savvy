"""
Core module — the one backend behind every surface.

The REPL, the Signal bot and scheduled check-ins all go through respond() /
build_context(), so they see the same prompt, rules, goals, facts, calendar and
history. The only thing a surface changes is the reply style (prompt.SURFACE_STYLE)
and its token budget.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import anthropic

from memory import Memory
from prompt import FACT_EXTRACTION_PROMPT, SYSTEM_PROMPT, SURFACE_STYLE, CONTEXT_TEMPLATE

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
from paths import load_savvy_rules
from tools import chat_with_tools, request_options
from tool_diagnostics import print_fact_saved
import goals
import meals


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
    """Gather calendar, email, diary, and rules context. All runs locally.

    Todoist is deliberately absent: those projects are an undated backlog of
    150+ items, so there is no small always-relevant slice worth injecting.
    Savvy reaches tasks through list_tasks instead.
    """
    ctx = {"calendar": "", "email": "", "diary": "", "rules": "", "goals": "", "meals": ""}

    try:
        ctx["goals"] = "MY GOALS:\n" + goals.format_goals_for_context()
    except Exception as e:
        print(f"[core] Goals unavailable: {e}")
    try:
        ctx["meals"] = "MEAL PLAN:\n" + meals.format_for_context()
    except Exception as e:
        print(f"[core] Meal plan unavailable: {e}")

    rules = load_savvy_rules()
    if rules:
        ctx["rules"] = "BEHAVIOR RULES:\n" + rules

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


def cache_marker(config: dict | None = None) -> dict:
    """The prompt-cache marker, TTL from config `prompt_cache_ttl` ("5m" or "1h").

    5m (the default) costs 1.25x to write and pays off from the second request
    that shares the prefix — every tool round within a turn, and messages a few
    minutes apart. 1h costs 2x to write and needs three requests an hour to win.
    """
    ttl = (config or load_app_config()).get("prompt_cache_ttl", "5m")
    return {"type": "ephemeral", "ttl": "1h"} if ttl == "1h" else {"type": "ephemeral"}


def build_context(user_input: str, memory: Memory, system_template: str = SYSTEM_PROMPT,
                  surface: str = "repl") -> tuple[list[dict], list[dict]]:
    """Build the system prompt and message list with all context.

    The system prompt is two blocks. The first — instructions, goals, rules and
    reply style — is identical request to request, so it carries the cache
    marker: tools render before system, so tools + this block (~13k tokens) are
    served from cache at a tenth of the price. The second holds everything that
    changes per turn (time, calendar, email, diary, recalled memories) and sits
    after the marker so it never invalidates the cached part.

    Args:
        user_input: Current user message
        memory: Memory instance
        system_template: The fixed instructions (no per-turn placeholders).
        surface: "repl" or "signal" — only selects the reply-style block.

    Returns:
        (system_blocks, messages)
    """
    config = load_app_config()
    context_top_k = config.get("context_top_k", 8)
    recent_window = config.get("recent_window", 10)

    live = get_live_context()

    # Goals sit in the cached part: they change rarely, and a change only costs
    # one cache rewrite.
    fixed = system_template
    for part in (live["goals"], live["rules"], SURFACE_STYLE.get(surface, "")):
        if part:
            fixed += "\n\n" + part

    dynamic = CONTEXT_TEMPLATE.format(
        datetime=datetime.now().strftime("%A, %B %d, %Y at %I:%M %p"),
        calendar_context=live["calendar"],
        email_context=live["email"],
        diary_context=live["diary"],
        meals_context=live["meals"],
    )

    # Semantic memory search (local embeddings)
    relevant = memory.retrieve_relevant(user_input, top_k=context_top_k)
    situation = memory.current_situation(annotate=True)
    relevant_facts = [f for f in memory.retrieve_facts(user_input, top_k=8, annotate=True) if f not in situation]

    memory_block = ""
    if relevant:
        memory_lines = []
        for msg in relevant:
            ts = datetime.fromtimestamp(msg["timestamp"]).strftime("%b %d, %I:%M %p")
            memory_lines.append(f"[{ts}] {msg['role']}: {msg['content'][:400]}")
        memory_block += "Relevant past conversations:\n" + "\n".join(memory_lines)

    if situation:
        memory_block += ("\n\nCurrent situation (recent, newest first — use it when prioritizing):\n"
                         + "\n".join(f"- {f}" for f in situation))
    if relevant_facts:
        memory_block += "\n\nKnown facts:\n" + "\n".join(f"- {f}" for f in relevant_facts)

    if memory_block:
        dynamic += f"\n\n<past_context>\n{memory_block}\n</past_context>"

    system = [
        {"type": "text", "text": fixed, "cache_control": cache_marker(config)},
        {"type": "text", "text": dynamic},
    ]

    # Recent sliding window
    messages = []
    recent = memory.retrieve_recent(n=recent_window)
    for msg in recent:
        messages.append({"role": msg["role"], "content": msg["content"]})
    # The API needs the first turn to be the user's. The window can open on an
    # assistant message — e.g. a check-in question Savvy texted unprompted —
    # and dropping it would lose exactly what the user is now answering.
    if messages and messages[0]["role"] == "assistant":
        messages.insert(0, {"role": "user", "content": "(earlier conversation)"})
    messages.append({"role": "user", "content": user_input})

    return system, messages


SURFACE_TAG = {"signal": "[Signal] "}


def respond(client: anthropic.Anthropic, memory: Memory, user_input: str, surface: str = "repl",
            config: dict | None = None, stream_callback=None, tool_event_callback=None,
            store_as: str | None = None) -> tuple[str, int, int]:
    """One conversational turn, identical on every surface.

    Builds context, runs the tool loop, stores both sides of the exchange and
    extracts facts. `store_as` overrides what is saved as the user's message
    (e.g. "[Morning Plan] ..." when the text sent to the model is a wrapped prompt).
    Returns (reply, input_tokens, output_tokens).
    """
    config = config or load_app_config()
    model = config.get("anthropic_model", "claude-sonnet-4-6")
    max_tokens = (config.get("signal_max_tokens", 2000) if surface == "signal"
                  else config.get("max_tokens", 1024))

    options, max_tokens = request_options(config, model, max_tokens)
    system, messages = build_context(user_input, memory, SYSTEM_PROMPT, surface)
    reply, tokens_in, tokens_out = chat_with_tools(
        client, model, system, messages, max_tokens=max_tokens,
        stream_callback=stream_callback, tool_event_callback=tool_event_callback,
        options=options,
    )
    reply = reply.strip()

    tag = SURFACE_TAG.get(surface, "")
    memory.store("user", tag + (store_as or user_input))
    memory.store("assistant", tag + (reply or "(action completed)"))

    # A failed extraction must never break a reply that already exists.
    try:
        facts = extract_facts(client, model, store_as or user_input, reply)
        if facts:
            print()  # off the end of the streamed reply
        for f in facts:
            print_fact_saved(f["fact"], f["type"], memory.store_fact(f["fact"], f["type"]))
    except Exception as e:
        print(f"[core] Fact extraction failed: {e}")
    return reply, tokens_in, tokens_out


# ---------------------------------------------------------------------------
# Fact extraction
# ---------------------------------------------------------------------------
def extract_facts(client: anthropic.Anthropic, model: str, user_msg: str,
                  assistant_msg: str) -> list[dict]:
    """Extract facts from a turn as [{"fact": str, "type": str}, ...].

    Each carries how long it stays true, so retrieval can fade a passing mood
    while leaving a health condition or a year-long goal at full weight.
    """
    try:
        prompt = FACT_EXTRACTION_PROMPT.format(
            user_message=user_msg, assistant_message=assistant_msg
        )
        # Extraction is simple: low effort when the model takes one, and read the
        # text block by type — on thinking models the first block can be a
        # (possibly empty) thinking block.
        config = load_app_config()
        extra = {"output_config": {"effort": "low"}} if config.get("effort") else {}
        resp = client.messages.create(
            model=model, max_tokens=2000 if extra else 512,
            messages=[{"role": "user", "content": prompt}], **extra,
        )
        text = next((b.text for b in resp.content if b.type == "text"), "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        facts = json.loads(text)
        if not isinstance(facts, list):
            return []

        out = []
        for item in facts:
            if isinstance(item, dict):
                fact = str(item.get("fact", "")).strip()
                ftype = str(item.get("type", "")).strip()
            else:
                # Tolerate the old bare-string format rather than losing the turn.
                fact, ftype = str(item).strip(), ""
            if fact:
                out.append({"fact": fact, "type": ftype or "situational"})
        return out
    except Exception as e:
        # Still never breaks the reply — but say so, or memory silently stops growing.
        print(f"[core] Fact extraction failed: {type(e).__name__}: {e}")
        return []
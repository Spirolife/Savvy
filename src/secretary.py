#!/usr/bin/env python3
"""
Savvy — Private Secretary
=========================
Desktop REPL and Signal bot in one file.

Usage:
    python secretary.py             # Desktop REPL
    python secretary.py --signal    # Signal bot (two-way texting)

Commands (REPL mode):
    /bod, /eod, /diary, /calendar, /email, /memory,
    /history, /facts, /extract, /model, /cost, /forget, /help, /quit
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime

import anthropic
from rich.console import Console
from rich.panel import Panel
from rich.theme import Theme

from memory import Memory
from prompt import SYSTEM_PROMPT, SIGNAL_SYSTEM_PROMPT, FACT_EXTRACTION_PROMPT
from core import (
    load_app_config, get_client, get_live_context, build_context,
    extract_facts, CALENDAR_AVAILABLE, EMAIL_AVAILABLE,
)
from tools import chat_with_tools
from diary import (
    store_entry, get_today_entries, get_recent_entries, format_entries_for_context,
)

if CALENDAR_AVAILABLE:
    from calendar_integration import (
        get_today_events, get_week_events, format_events_for_context,
    )
if EMAIL_AVAILABLE:
    from email_integration import (
        get_unread_count, get_recent_emails, format_emails_for_context,
    )

from notifier import (
    load_config as load_signal_config, check_signal_cli,
    send_message, receive_messages,
)

console = Console(theme=Theme({"info": "dim cyan", "warning": "bold yellow"}))
session_input_tokens = 0
session_output_tokens = 0


# ===================================================================
# REPL MODE
# ===================================================================
def handle_bod(client, model, mem):
    console.print(Panel("What's your plan for today?", title="Morning Plan", border_style="cyan"))
    if CALENDAR_AVAILABLE:
        try:
            events = get_today_events()
            if events:
                console.print(f"[info]Today's calendar:[/]")
                console.print(f"[info]{format_events_for_context(events)}[/]\n")
        except Exception:
            pass

    lines = []
    console.print("[dim](Type your plan, press Enter twice to finish)[/]")
    while True:
        try:
            line = input("> " if not lines else "  ")
        except (EOFError, KeyboardInterrupt):
            break
        if line == "" and lines:
            break
        lines.append(line)

    if not lines:
        console.print("[info]Cancelled.[/]")
        return

    content = "\n".join(lines)
    system, messages = build_context(
        f"Here is my morning plan for today:\n\n{content}\n\n"
        "Acknowledge briefly. Flag conflicts with my calendar or forgotten goals. 2-3 sentences.",
        mem, SYSTEM_PROMPT,
    )

    console.print("\n[bold green]Savvy:[/] ", end="")
    response, in_t, out_t = chat_with_tools(
        client, model, system, messages,
        stream_callback=lambda t: console.print(t, end="", highlight=False),
    )
    console.print()

    store_entry("bod", content, response)
    mem.store("user", f"[Morning Plan] {content}")
    mem.store("assistant", response)
    facts = extract_facts(client, model, content, response)
    for f in facts:
        mem.store_fact(f)
    console.print()


def handle_eod(client, model, mem):
    today_entries = get_today_entries()
    bod_content = None
    for e in today_entries:
        if e["type"] == "bod":
            bod_content = e["content"]
            break

    if bod_content:
        console.print(Panel(f"This morning you planned:\n{bod_content[:500]}", title="Your Morning Plan", border_style="dim"))

    console.print(Panel("How did today go?", title="Evening Reflection", border_style="cyan"))
    lines = []
    console.print("[dim](Type your reflection, press Enter twice to finish)[/]")
    while True:
        try:
            line = input("> " if not lines else "  ")
        except (EOFError, KeyboardInterrupt):
            break
        if line == "" and lines:
            break
        lines.append(line)

    if not lines:
        console.print("[info]Cancelled.[/]")
        return

    content = "\n".join(lines)
    bod_ref = f"\n\nFor reference, this morning they planned:\n{bod_content}" if bod_content else ""
    system, messages = build_context(
        f"Here is my evening reflection:{bod_ref}\n\n{content}\n\n"
        "Acknowledge. Compare planned vs actual. Note patterns. Suggest for tomorrow. 3-4 sentences.",
        mem, SYSTEM_PROMPT,
    )

    console.print("\n[bold green]Savvy:[/] ", end="")
    response, _, _ = chat_with_tools(
        client, model, system, messages,
        stream_callback=lambda t: console.print(t, end="", highlight=False),
    )
    console.print()

    store_entry("eod", content, response)
    mem.store("user", f"[Evening Reflection] {content}")
    mem.store("assistant", response)
    facts = extract_facts(client, model, content, response)
    for f in facts:
        mem.store_fact(f)
    console.print()


def handle_command(cmd, mem, client, model):
    global session_input_tokens, session_output_tokens
    c = cmd.strip().lower()

    if c == "/quit":
        console.print(f"\n[info]Session: {session_input_tokens:,} in / {session_output_tokens:,} out[/]")
        mem.close()
        sys.exit(0)
    elif c == "/help":
        console.print(Panel(
            "/bod      — Morning plan\n/eod      — Evening reflection\n"
            "/diary    — Recent diary\n/calendar — Today's calendar\n"
            "/email    — Recent emails\n/memory   — Memory stats\n"
            "/history  — Conversation history\n/facts    — Stored facts\n"
            "/extract  — Extract facts from last exchange\n"
            "/model    — Model info\n/cost     — Token usage\n"
            "/forget   — Clear memory\n/quit     — Exit", title="Commands"))
    elif c == "/bod":
        handle_bod(client, model, mem)
    elif c == "/eod":
        handle_eod(client, model, mem)
    elif c == "/diary":
        entries = get_recent_entries(days=7)
        if not entries:
            console.print("[info]No diary entries. Use /bod and /eod.[/]")
        else:
            for e in entries:
                ts = datetime.fromtimestamp(e["timestamp"]).strftime("%b %d %-I:%M %p")
                label = {"bod": "Morning Plan", "eod": "Evening Reflection", "note": "Note"}.get(e["type"], e["type"])
                color = "cyan" if e["type"] == "bod" else "magenta" if e["type"] == "eod" else "white"
                console.print(f"[bold {color}][{ts}] {label}:[/] {e['content'][:300]}")
                if e.get("response"):
                    console.print(f"  [dim]Savvy: {e['response'][:200]}[/]")
                console.print()
    elif c == "/calendar":
        if not CALENDAR_AVAILABLE:
            console.print("[warning]Calendar not set up.[/]")
        else:
            events = get_today_events()
            console.print(Panel(format_events_for_context(events), title="Today") if events else "[info]Nothing today.[/]")
    elif c == "/email":
        if not EMAIL_AVAILABLE:
            console.print("[warning]Gmail not set up.[/]")
        else:
            for label, count in get_unread_count().items():
                console.print(f"[info]{label}: {count} unread[/]")
            emails = get_recent_emails(max_results=10, hours_back=24)
            if emails:
                console.print(Panel(format_emails_for_context(emails), title="Recent (24h)"))
    elif c == "/memory":
        s = mem.get_stats()
        console.print(Panel(f"Messages: {s['total_messages']}\nFacts: {s['total_facts']}\nSessions: {s['total_sessions']}", title="Memory"))
    elif c == "/history":
        for msg in mem.retrieve_recent(n=20):
            ts = datetime.fromtimestamp(msg["timestamp"]).strftime("%b %d %I:%M %p")
            rc = "bold cyan" if msg["role"] == "user" else "bold green"
            console.print(f"[{rc}][{ts}] {msg['role']}:[/] {msg['content'][:200]}")
    elif c == "/facts":
        facts = mem.retrieve_facts("", top_k=30)
        for i, f in enumerate(facts, 1):
            console.print(f"  {i}. {f}")
        if not facts:
            console.print("[info]No facts yet.[/]")
    elif c == "/model":
        console.print(f"[info]LLM: {model} | Calendar: {'yes' if CALENDAR_AVAILABLE else 'no'} | Email: {'yes' if EMAIL_AVAILABLE else 'no'}[/]")
    elif c == "/cost":
        total = session_input_tokens + session_output_tokens
        console.print(Panel(f"In: {session_input_tokens:,}\nOut: {session_output_tokens:,}\nTotal: {total:,}", title="Tokens"))
    elif c == "/forget":
        if input("Delete all memory? (yes/no): ").strip().lower() == "yes":
            mem.db.execute("DELETE FROM conversations")
            mem.db.execute("DELETE FROM facts")
            mem.db.commit()
            console.print("[warning]Memory cleared.[/]")
    else:
        return False
    return True


def run_repl():
    global session_input_tokens, session_output_tokens
    config = load_app_config()
    model = config.get("anthropic_model", "claude-sonnet-4-6")

    console.print(Panel.fit(
        "[bold]Savvy — Private Secretary[/]\n"
        "[dim]Calendar • Gmail • Diary • Signal • Local Memory[/]\n"
        "[dim]/help for commands, /quit to exit[/]", border_style="cyan"))

    try:
        client = get_client(config)
    except ValueError as e:
        console.print(f"[warning]{e}[/]")
        sys.exit(1)

    console.print("[info]API...[/]", end=" ")
    try:
        client.messages.create(model=model, max_tokens=10, messages=[{"role": "user", "content": "ping"}])
        console.print(f"[info]connected ({model})[/]")
    except Exception as e:
        console.print(f"[warning]{e}[/]")
        sys.exit(1)

    if CALENDAR_AVAILABLE:
        from google_auth import get_account_labels
        console.print(f"[info]Google: {', '.join(get_account_labels())}[/]")

    try:
        import httpx
        httpx.get("http://localhost:11434/api/tags", timeout=5.0)
        console.print("[info]Ollama: running[/]")
    except Exception:
        console.print("[dim]Ollama: off (recency-only memory)[/]")
    console.print()

    mem = Memory()
    last_u, last_a = "", ""

    while True:
        try:
            inp = get_user_input()
        except (EOFError, KeyboardInterrupt):
            console.print(f"\n[info]{session_input_tokens:,} in / {session_output_tokens:,} out[/]")
            mem.close()
            break

        if not inp:
            continue

        if inp.startswith("/"):
            if inp.strip().lower() == "/extract":
                if last_u and last_a:
                    for f in extract_facts(client, model, last_u, last_a):
                        mem.store_fact(f)
                        console.print(f"  [info]Stored: {f}[/]")
                continue
            if handle_command(inp, mem, client, model):
                continue

        system, messages = build_context(inp, mem, SYSTEM_PROMPT)
        console.print("[bold green]Savvy:[/] ", end="")
        try:
            response, in_t, out_t = chat_with_tools(
                client, model, system, messages,
                stream_callback=lambda t: console.print(t, end="", highlight=False),
            )
            session_input_tokens += in_t
            session_output_tokens += out_t
            console.print()
        except Exception as e:
            console.print(f"[warning]{e}[/]")
            continue

        mem.store("user", inp)
        mem.store("assistant", response)
        last_u, last_a = inp, response

        for f in extract_facts(client, model, inp, response):
            mem.store_fact(f)
        console.print()

def get_user_input() -> str:
    """Read user input, supporting multi-line paste."""
    lines = []
    first = input("You: ").strip()
    if not first:
        return ""
    lines.append(first)
    
    # Check if more lines are waiting (pasted text)
    import select, sys
    while select.select([sys.stdin], [], [], 0.05)[0]:
        line = sys.stdin.readline()
        if line:
            lines.append(line.rstrip('\n'))
        else:
            break
    
    return "\n".join(lines)
    
# ===================================================================
# SIGNAL BOT MODE
# ===================================================================
def run_signal():
    logger = logging.getLogger("savvy.signal")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    config = load_app_config()
    sig = load_signal_config()
    model = config.get("anthropic_model", "claude-sonnet-4-6")

    try:
        client = get_client(config)
    except ValueError as e:
        logger.error(str(e))
        return

    sender = sig.get("sender_number", "")
    allowed = sig.get("recipient_number", "")
    if not sender:
        logger.error("Set sender_number in config.json")
        return

    status = check_signal_cli(sig)
    if not status["ok"]:
        logger.error(f"signal-cli: {status['error']}")
        return
    logger.info(f"signal-cli: {status['version']}")

    try:
        client.messages.create(model=model, max_tokens=10, messages=[{"role": "user", "content": "ping"}])
        logger.info(f"Claude: connected ({model})")
    except Exception as e:
        logger.error(f"Claude: {e}")
        return

    logger.info(f"Signal Bot started — {sender}")
    logger.info(f"Calendar: {'yes' if CALENDAR_AVAILABLE else 'no'} | Email: {'yes' if EMAIL_AVAILABLE else 'no'}")

    mem = Memory(session_id="signal_bot")
    convo: list[dict] = []
    MAX_H = 10
    seen = set()

    while True:
        try:
            for msg in receive_messages(sig):
                source, text, ts = msg["source"], msg["message"], msg.get("timestamp", 0)
                if ts in seen:
                    continue
                seen.add(ts)
                if allowed and source != allowed:
                    continue

                logger.info(f"In: {text[:100]}")

                # Build context
                live = get_live_context()
                ctx = [f"Current time: {datetime.now().strftime('%A, %B %d, %Y at %I:%M %p')}"]
                for v in [live["calendar"], live["email"], live["diary"]]:
                    if v:
                        ctx.append(v)

                relevant = mem.retrieve_relevant(text, top_k=5)
                if relevant:
                    ctx.append("RELEVANT PAST:\n" + "\n".join(
                        f"[{datetime.fromtimestamp(m['timestamp']).strftime('%b %d %I:%M %p')}] {m['role']}: {m['content'][:200]}"
                        for m in relevant))

                facts = mem.retrieve_facts(text, top_k=8)
                if facts:
                    ctx.append("KNOWN FACTS:\n" + "\n".join(f"- {f}" for f in facts))

                system = SIGNAL_SYSTEM_PROMPT.format(context="\n\n".join(ctx))
                msgs = list(convo[-MAX_H:]) + [{"role": "user", "content": text}]

                try:
                    reply, _, _ = chat_with_tools(client, model, system, msgs, max_tokens=500)
                    reply = reply.strip() or "(done)"
                except Exception as e:
                    logger.error(f"Claude: {e}")
                    reply = "Sorry, hit an error. Try again."

                mem.store("user", f"[Signal] {text}")
                mem.store("assistant", f"[Signal] {reply}")
                convo.append({"role": "user", "content": text})
                convo.append({"role": "assistant", "content": reply})
                while len(convo) > MAX_H * 2:
                    convo.pop(0)

                logger.info(f"Out: {reply[:100]}")
                send_message(reply, sig)

                for f in extract_facts(client, model, text, reply):
                    mem.store_fact(f)

            if len(seen) > 1000:
                seen = set(sorted(seen)[-500:])

        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
        time.sleep(5)


# ===================================================================
# Entry
# ===================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Savvy — Private Secretary")
    parser.add_argument("--signal", action="store_true", help="Signal bot mode")
    args = parser.parse_args()

    if args.signal:
        print("=" * 50)
        print("  Savvy — Signal Bot")
        print("  Ctrl+C to stop")
        print("=" * 50)
        try:
            run_signal()
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        run_repl()
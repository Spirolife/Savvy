"""
User-side slash commands for controlling tool diagnostics at runtime.

These commands are intercepted before input reaches the LLM. They never
appear in the conversation history.

Commands:
    /danger <tool> <0|1|2>    Set a tool's danger level (persists to config)
    /danger show              Show all current danger levels
    /allow <tool>             Bypass confirmation for this tool this session
    /revoke <tool>            Remove a tool from the session allowlist
    /reload                   Re-read tool_config.json from disk
    /tool-help                Show this help

Future-proofing: this module is the right place to add more runtime
toggles (per-section overrides, temporary suspensions, etc.) without
touching tool_diagnostics.py or tools.py.
"""

from tool_diagnostics import (
    get_danger_level, set_danger_level,
    add_to_allowlist, remove_from_allowlist,
    snapshot_config, reload_config,
    LEVEL_LABELS,
)

USER_COMMANDS = {
    "/danger", "/allow", "/revoke", "/reload", "/tool-help",
}


def is_user_command(text: str) -> bool:
    """True if `text` begins with a known user-tools slash command."""
    if not text or not text.startswith("/"):
        return False
    head = text.strip().split()[0].lower()
    return head in USER_COMMANDS


def handle_user_command(text: str) -> bool:
    """Parse and execute a user-tools command. Returns True if handled,
    False if the input wasn't a recognized command (caller should pass
    it on to the LLM or to existing /bod /eod /etc. handlers)."""
    if not is_user_command(text):
        return False

    parts = text.strip().split()
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd == "/tool-help":
        _print_help()
        return True

    if cmd == "/reload":
        reload_config()
        print("[user_tools] Reloaded tool_config.json from disk.")
        return True

    if cmd == "/danger":
        _handle_danger(args)
        return True

    if cmd == "/allow":
        if len(args) != 1:
            print("[user_tools] Usage: /allow <tool_name>")
            return True
        add_to_allowlist(args[0])
        print(f"[user_tools] '{args[0]}' added to session allowlist.")
        return True

    if cmd == "/revoke":
        if len(args) != 1:
            print("[user_tools] Usage: /revoke <tool_name>")
            return True
        remove_from_allowlist(args[0])
        print(f"[user_tools] '{args[0]}' removed from session allowlist.")
        return True

    # Should be unreachable given is_user_command()
    return False


def _handle_danger(args: list[str]) -> None:
    if not args:
        print("[user_tools] Usage: /danger <tool> <0|1|2>  |  /danger show")
        return

    if args[0].lower() == "show":
        snap = snapshot_config()
        print("\nCurrent danger levels:")
        for tool, level in snap["danger_levels"].items():
            label = LEVEL_LABELS.get(level, "?")
            print(f"  {level} ({label:8}) {tool}")
        if snap["session_allowlist"]:
            print("\nSession allowlist:")
            for t in snap["session_allowlist"]:
                print(f"  {t}")
        else:
            print("\nSession allowlist: (empty)")
        return

    if len(args) != 2:
        print("[user_tools] Usage: /danger <tool> <0|1|2>  |  /danger show")
        return

    tool, level_str = args
    try:
        level = int(level_str)
    except ValueError:
        print(f"[user_tools] Level must be 0, 1, or 2 (got {level_str!r}).")
        return
    try:
        old_level = get_danger_level(tool)
        set_danger_level(tool, level)
        old_label = LEVEL_LABELS.get(old_level, "?")
        new_label = LEVEL_LABELS.get(level, "?")
        print(f"[user_tools] {tool}: {old_level} ({old_label}) → {level} ({new_label})")
    except ValueError as e:
        print(f"[user_tools] {e}")


def _print_help() -> None:
    print("""
User-tools commands (intercepted before LLM):
  /danger <tool> <0|1|2>   Set tool danger level. 0=read, 1=internal, 2=external.
  /danger show             List all current danger levels and the allowlist.
  /allow <tool>            Bypass confirmation for this tool until config changes.
  /revoke <tool>           Remove from allowlist.
  /reload                  Re-read tool_config.json from disk.
  /tool-help               Show this help.

Confirmation rules:
  Level 0  read-only       Run silently, one-line print.
  Level 1  internal write  Run silently UNLESS the LLM flagged the call as
                           important; then prompt before executing.
  Level 2  external write  Always prompt before executing.
""")
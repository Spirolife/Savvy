#!/usr/bin/env python3
"""
configure.py — interactive wizard that writes everything in credentials/.

Run from anywhere (stdlib only, so it works before the venv exists):
    python3 setup/configure.py

Walks through, in order, and lets you skip any section:
  1. Anthropic      — API key (verified live), model, effort
  2. Google         — credentials.json from Cloud Console, then one browser
                      sign-in per account label (token_<label>.json)
  3. Signal         — reads the linked number off the signal-api container
  4. Scheduler      — quiet hours, notification budget, BOD/EOD/check-in times
  5. Todoist/Travel — hands off to setup-todoist.py / setup-travel.py
  6. Tool safety    — credentials/tool_config.json danger levels
  7. Rules          — starter lines for notes/savvy_rules.md

Safe to re-run: existing values are shown as defaults and kept unless you
change them. config.json is written chmod 600 since it holds secrets.
"""

import getpass
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
CRED_DIR = ROOT / "credentials"
CONFIG_PATH = CRED_DIR / "config.json"
GOOGLE_CLIENT = CRED_DIR / "credentials.json"
TOOL_CONFIG = CRED_DIR / "tool_config.json"
RULES_PATH = ROOT / "notes" / "savvy_rules.md"
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
SETUP_CMD = r"setup\setup.ps1" if os.name == "nt" else "./setup/setup.sh"
LAUNCH_CMD = r"setup\launch.ps1" if os.name == "nt" else "./setup/launch.sh"

BOLD, DIM, GREEN, YELLOW, RED, RESET = "\033[1m", "\033[2m", "\033[92m", "\033[93m", "\033[91m", "\033[0m"


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------
def header(title: str) -> None:
    print(f"\n{BOLD}{'=' * 55}\n  {title}\n{'=' * 55}{RESET}")


def ok(msg): print(f"{GREEN}[✓]{RESET} {msg}")
def warn(msg): print(f"{YELLOW}[!]{RESET} {msg}")
def fail(msg): print(f"{RED}[✗]{RESET} {msg}")


def ask(prompt: str, default=None, validate=None) -> str:
    """Prompt with a default shown in brackets; re-ask until `validate` passes."""
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        ans = input(f"{prompt}{suffix}: ").strip()
        if not ans and default is not None:
            ans = str(default)
        if validate is None:
            return ans
        err = validate(ans)
        if not err:
            return ans
        fail(err)


def ask_yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    ans = input(f"{prompt} [{hint}]: ").strip().lower()
    return default if not ans else ans.startswith("y")


def keep_existing(summary: str, default: bool = True) -> bool:
    """Show what's already configured; True means skip this section."""
    ok(f"Already set up: {summary}")
    return ask_yes("Keep it and skip this step?", default)


def ask_secret(prompt: str, current: str = "") -> str:
    """Hidden input; Enter keeps the current value."""
    hint = f" (Enter keeps …{current[-4:]})" if current else ""
    ans = getpass.getpass(f"{prompt}{hint}: ").strip()
    return ans or current


def ask_int(prompt: str, default: int, lo: int, hi: int) -> int:
    def v(s):
        return None if s.isdigit() and lo <= int(s) <= hi else f"Enter a whole number {lo}-{hi}."
    return int(ask(prompt, default, v))


def valid_time(s: str):
    return None if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s) else "Use 24h HH:MM, e.g. 08:30."


def valid_phone(s: str):
    return None if re.fullmatch(r"\+\d{7,15}", s) else "Use international format with country code, e.g. +15551234567."


# ---------------------------------------------------------------------------
# Config file I/O
# ---------------------------------------------------------------------------
def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> None:
    CRED_DIR.mkdir(exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    CONFIG_PATH.chmod(0o600)


def http_json(url: str, headers: dict | None = None, timeout: float = 10.0):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------------------
# 1. Anthropic
# ---------------------------------------------------------------------------
MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1", "claude-haiku-5-5"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]


def check_anthropic_key(key: str) -> bool:
    try:
        http_json("https://api.anthropic.com/v1/models",
                  {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return True
    except urllib.error.HTTPError as e:
        fail(f"Anthropic rejected the key (HTTP {e.code}).")
    except urllib.error.URLError as e:
        warn(f"Could not reach Anthropic to verify ({e.reason}) — saving anyway.")
        return True
    return False


def setup_anthropic(cfg: dict) -> None:
    header("1. Anthropic API")
    key = cfg.get("anthropic_api_key", "")
    if key and keep_existing(f"key …{key[-4:]}, model {cfg.get('anthropic_model', 'default')}"):
        return
    print("Get a key at https://console.anthropic.com/ → API Keys.")
    while True:
        key = ask_secret("Anthropic API key", cfg.get("anthropic_api_key", ""))
        if not key:
            warn("No key — Savvy can't run without one. Skipping for now.")
            return
        if check_anthropic_key(key):
            cfg["anthropic_api_key"] = key
            ok("Key verified.")
            break
        if not ask_yes("Try a different key?"):
            return

    print(f"\nModels: {', '.join(MODELS)}")
    cfg["anthropic_model"] = ask("Model", cfg.get("anthropic_model", MODELS[0]))
    effort = ask(f"Effort ({'/'.join(EFFORTS)}, or 'none' for model default)",
                 cfg.get("effort", "medium"),
                 lambda s: None if s in EFFORTS + ["none"] else "Pick one of the listed values.")
    if effort == "none":
        cfg.pop("effort", None)
    else:
        cfg["effort"] = effort
    cfg["max_tokens"] = ask_int("Max reply tokens", cfg.get("max_tokens", 1024), 256, 64000)
    cfg.setdefault("context_top_k", 8)
    cfg.setdefault("recent_window", 10)
    save_config(cfg)
    ok(f"Saved to {CONFIG_PATH.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# 2. Google
# ---------------------------------------------------------------------------
GOOGLE_STEPS = """\
  1. https://console.cloud.google.com/ → create (or pick) a project
  2. APIs & Services → Library → enable: Google Calendar API, Gmail API,
     Google Tasks API
  3. OAuth consent screen → External → fill required fields →
     Test users: add every Gmail address Savvy should manage
  4. Credentials → Create credentials → OAuth client ID → Desktop app
  5. Download JSON (it lands in ~/Downloads as client_secret_….json)"""


def find_downloaded_client() -> Path | None:
    hits = sorted([*CRED_DIR.glob("client_secret_*.json"),
                   *(Path.home() / "Downloads").glob("client_secret_*.json")],
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def install_google_client() -> bool:
    print(GOOGLE_STEPS)
    guess = find_downloaded_client()
    while True:
        raw = ask("\nPath to the downloaded JSON (blank to skip Google)", str(guess) if guess else "")
        if not raw:
            return False
        src = Path(raw).expanduser()
        data = load_json(src)
        if "installed" not in data:
            fail("That isn't a Desktop-app OAuth client file (no 'installed' section). "
                 "Make sure the application type was 'Desktop app'.")
            continue
        shutil.copy(src, GOOGLE_CLIENT)
        GOOGLE_CLIENT.chmod(0o600)
        ok(f"Copied to {GOOGLE_CLIENT.relative_to(ROOT)}")
        return True


def existing_labels() -> list[str]:
    return sorted(p.stem.removeprefix("token_") for p in CRED_DIR.glob("token_*.json"))


def setup_google(cfg: dict) -> None:
    header("2. Google Calendar / Gmail / Tasks")
    labels = existing_labels()
    if GOOGLE_CLIENT.exists() and labels and keep_existing(f"accounts {', '.join(labels)}"):
        return
    if not ask_yes("Set up Google integration?"):
        return

    if GOOGLE_CLIENT.exists():
        ok("credentials.json already present.")
    elif not install_google_client():
        warn("Skipped. Re-run this script once you have the client JSON.")
        return

    if not VENV_PY.exists():
        warn(f"No venv at {VENV_PY.parent.parent.relative_to(ROOT)}/ — run {SETUP_CMD} first, "
             "then re-run this to authorize accounts.")
        return

    labels = existing_labels()
    if labels:
        ok(f"Already authorized: {', '.join(labels)}")
    print("\nEach account gets a short label (e.g. personal, work, school).")
    print("A browser opens for each — sign in with THAT account.")
    while True:
        label = ask("Label to authorize (blank when done)", "",
                    lambda s: None if not s or re.fullmatch(r"[a-z0-9_-]+", s)
                    else "Lowercase letters, digits, - and _ only.")
        if not label:
            break
        if label in labels and not ask_yes(f"'{label}' already exists — re-authorize?", False):
            continue
        r = subprocess.run([str(VENV_PY), str(SRC / "google_auth.py"), label], cwd=SRC)
        if r.returncode == 0:
            labels = existing_labels()
        else:
            fail("Authorization failed. Most common cause: that account isn't a test user "
                 "on the OAuth consent screen.")

    labels = existing_labels()
    if len(labels) > 1:
        print(f"\nAccounts: {', '.join(labels)}")
        print("Where should new calendar events go when you don't name an account?")
        cur = cfg.get("calendar_account_lock") or cfg.get("default_calendar_account") or labels[0]
        default = ask("Default calendar account", cur,
                      lambda s: None if s in labels else f"Pick one of: {', '.join(labels)}")
        if ask_yes("Lock ALL event creation to that account (only if you never use the others)?",
                   bool(cfg.get("calendar_account_lock"))):
            cfg["calendar_account_lock"] = default
            cfg.pop("default_calendar_account", None)
        else:
            cfg["default_calendar_account"] = default
            cfg.pop("calendar_account_lock", None)
        save_config(cfg)
    if labels:
        ok(f"Google ready: {', '.join(labels)}")


# ---------------------------------------------------------------------------
# 3. Signal
# ---------------------------------------------------------------------------
def signal_call(method: str, url: str, body: dict | None = None) -> tuple[bool, str]:
    """POST/PUT to signal-cli-rest-api. Returns (ok, error text from the API)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60):
            return True, ""
    except urllib.error.HTTPError as e:
        try:
            return False, json.loads(e.read().decode()).get("error", f"HTTP {e.code}")
        except Exception:
            return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, str(e.reason)


CAPTCHA_URL = "https://signalcaptchas.org/registration/generate.html"


def ask_captcha() -> str:
    print(f"\n  Signal wants a captcha before it sends the code:")
    print(f"  1. Open {CAPTCHA_URL}")
    print("  2. Solve it. The page then tries to open Signal — instead, right-click the")
    print("     'Open Signal' link and Copy Link (it starts with signalcaptcha://)")
    tok = ask("  Paste the link", "", lambda t: None if t.startswith("signalcaptcha://")
              else "It should start with signalcaptcha://")
    return tok.removeprefix("signalcaptcha://")


def register_number(url: str, number: str) -> bool:
    """Register `number` as a standalone Signal account owned by the container."""
    captcha = ""
    while True:
        body = {"use_voice": False, **({"captcha": captcha} if captcha else {})}
        sent, err = signal_call("POST", f"{url}/v1/register/{number}", body)
        if sent:
            ok("Verification code requested by SMS.")
            break
        if "captcha" in err.lower():
            captcha = ask_captcha()
            continue
        fail(f"Signal refused: {err}")
        if not ask_yes("Try again?"):
            return False
        captcha = ""

    while True:
        code = ask("Code from the SMS (blank = didn't arrive)", "").replace("-", "").replace(" ", "")
        if not code:
            # Landlines and some VoIP numbers can't take SMS; Signal only
            # allows the call after an SMS attempt and a ~60s wait.
            if ask_yes("Get a voice call instead? (wait ~60s after the SMS request first)"):
                return register_number_voice(url, number, captcha)
            return False
        done, err = signal_call("POST", f"{url}/v1/register/{number}/verify/{code}", {})
        if done:
            return True
        if "pin" in err.lower() or "lock" in err.lower():
            pin = getpass.getpass("This number has a Signal registration-lock PIN. PIN: ").strip()
            done, err = signal_call("POST", f"{url}/v1/register/{number}/verify/{code}", {"pin": pin})
            if done:
                return True
        fail(f"Verification failed: {err}")
        if not ask_yes("Try another code?"):
            return False


def register_number_voice(url: str, number: str, captcha: str) -> bool:
    body = {"use_voice": True, **({"captcha": captcha} if captcha else {})}
    sent, err = signal_call("POST", f"{url}/v1/register/{number}", body)
    if not sent and "captcha" in err.lower():
        body["captcha"] = ask_captcha()
        sent, err = signal_call("POST", f"{url}/v1/register/{number}", body)
    if not sent:
        fail(f"Signal refused the voice call: {err}. Wait a minute and re-run this step.")
        return False
    ok("Calling now.")
    code = ask("Code from the call", "", lambda c: None if c else "Enter the code.").replace("-", "").replace(" ", "")
    done, err = signal_call("POST", f"{url}/v1/register/{number}/verify/{code}", {})
    if not done:
        fail(f"Verification failed: {err}")
    return done


def pair_phone(url: str, bot: str) -> tuple[str, str] | None:
    """Prove which Signal account is the user's: they text a one-time code to the bot.

    Needed because a user with "Who can see my number: Nobody" reaches the
    bot as a bare UUID, which no phone-number allowlist can match. Returns
    (number or "", uuid), or None if nothing arrived.
    """
    code = f"{random.randint(1000, 9999)}"
    print(f"\n{BOLD}Pair your phone:{RESET} from your Signal, text  {BOLD}pair {code}{RESET}  to {bot}")
    print(f"{DIM}(If Savvy is already running, stop it first — it would read the message instead.){RESET}")
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            envelopes = http_json(f"{url}/v1/receive/{bot}", timeout=30) or []
        except Exception:
            envelopes = []
        for w in envelopes:
            env = w.get("envelope", {})
            text = (env.get("dataMessage") or {}).get("message") or ""
            if code in text and env.get("sourceUuid"):
                return env.get("sourceNumber") or "", env["sourceUuid"]
        time.sleep(3)
    return None


def setup_signal(cfg: dict) -> None:
    header("3. Signal bot")
    if cfg.get("sender_number") and cfg.get("recipient_number"):
        rcpt = cfg["recipient_number"]
        rcpt = ", ".join(rcpt) if isinstance(rcpt, list) else rcpt
        note_to_self = rcpt == cfg["sender_number"]
        if note_to_self:
            warn("Savvy is using your own account, so its messages land in Note to Self. "
                 "Reconfigure to give it a separate number.")
        if keep_existing(f"bot {cfg['sender_number']}, replies to {rcpt}", not note_to_self):
            return
    print("Lets you text Savvy from your phone. There's no account or API key to get:")
    print("the 'Signal API' is signal-cli-rest-api, a free open-source container")
    print("(github.com/bbernhard/signal-cli-rest-api) that runs locally under Podman or Docker.")
    print(f"{DIM}Needs: Signal on your phone. Podman or Docker (offered if missing).{RESET}")
    if not ask_yes("\nSet up the Signal texting interface?"):
        return

    url = cfg.get("signal_api_url", "http://localhost:8080").rstrip("/")

    def api_up() -> bool:
        try:
            http_json(f"{url}/v1/about", timeout=3)
            return True
        except Exception:
            return False

    def accounts() -> list[str]:
        try:
            return http_json(f"{url}/v1/accounts", timeout=5) or []
        except Exception:
            return []

    if api_up():
        ok(f"Signal container is already running at {url}.")
    elif ask_yes("Container isn't running. Download and start it now (~400 MB, via setup-signal.py)?"):
        r = subprocess.run([sys.executable, str(ROOT / "setup" / "setup-signal.py")])
        if r.returncode != 0 or not api_up():
            fail("Container didn't come up — check `podman logs signal-api` (or `docker logs signal-api`), then re-run this script.")
            return
        print(f"\n{DIM}(Ignore the manual steps printed above — this wizard handles them.){RESET}")
    else:
        url = ask("Already running it elsewhere? Its URL (blank to skip Signal)", "").rstrip("/")
        if not url or not api_up():
            warn("Skipped Signal.")
            return
    cfg["signal_api_url"] = url

    print(f"\n{BOLD}How should Savvy appear in Signal?{RESET}")
    print("  1. Its own number (recommended) — Savvy is a separate contact you chat with.")
    print("     Needs a second number that can get an SMS or call: spare SIM, Google Voice,")
    print("     a landline, etc. It must NOT be the number your phone's Signal uses —")
    print("     registering that here would log your phone out.")
    print("  2. Your own account — links as a device; Savvy's messages land in Note to Self.")
    mode = ask("Choose 1 or 2", "1", lambda s: None if s in ("1", "2") else "Enter 1 or 2.")

    existing = accounts()
    if mode == "1":
        mine = ask("YOUR personal Signal number (the one Savvy should talk to)",
                   cfg.get("recipient_number") if isinstance(cfg.get("recipient_number"), str) else None,
                   valid_phone)
        bot_default = next((a for a in existing if a != mine), None)
        if bot_default:
            ok(f"The container already has {bot_default} registered.")
        bot = ask("Savvy's number", bot_default,
                  lambda s: valid_phone(s) or ("That's your own number — Savvy needs a different one."
                                               if s == mine else None))
        if bot not in existing:
            if not register_number(url, bot):
                warn("Not registered. Re-run this script to try again.")
                return
            ok(f"{bot} registered to Savvy.")
            name = ask("Display name for Savvy in Signal", "Savvy")
            signal_call("PUT", f"{url}/v1/profiles/{bot}", {"name": name})
        if mine in existing:
            warn(f"The container is also linked to your own number {mine} from an earlier setup.")
            print("    Remove it on your phone: Signal → Settings → Linked devices → remove 'savvy'.")
        cfg["sender_number"] = bot
        cfg["recipient_number"] = mine
        save_config(cfg)

        while True:
            paired = pair_phone(url, bot)
            if paired:
                break
            warn("No pairing message arrived within 3 minutes.")
            if not ask_yes("Try again?"):
                warn("Not paired. If your number is hidden in Signal's privacy settings, "
                     "Savvy will ignore you until you re-run this step.")
                return
        number, uuid = paired
        cfg["recipient_uuid"] = uuid
        save_config(cfg)
        if number and number != mine:
            warn(f"That message came from {number}, not {mine}. Using the account that sent it.")
            cfg["recipient_number"] = number
            save_config(cfg)
        ok(f"Paired with your Signal account{' (' + number + ')' if number else ' (number hidden — matched by account ID)'}.")
        signal_call("POST", f"{url}/v2/send",
                    {"number": bot, "recipients": [uuid], "message": "Paired with Savvy ✓"})
        ok(f"Sent a confirmation to your phone. Save {bot} as a contact and text it to talk to Savvy.")
        return

    if not existing:
        print(f"\n{BOLD}Link your phone:{RESET}")
        print(f"  1. Open {url}/v1/qrcodelink?device_name=savvy in a browser")
        print("  2. Phone: Signal → Settings → Linked devices → Link new device")
        print("  3. Scan the QR code (it expires after a minute or so — refresh if needed)")
        while not existing:
            input("Press Enter once linked… ")
            existing = accounts()
            if not existing:
                warn("No linked account yet.")
                if not ask_yes("Check again?"):
                    break
    num = ask("Your Signal number (the linked one)",
              cfg.get("sender_number") or (existing[0] if existing else None), valid_phone)
    cfg["sender_number"] = num
    cfg["recipient_number"] = num
    save_config(cfg)
    ok("Saved. Talk to Savvy in your Note to Self chat.")


# ---------------------------------------------------------------------------
# 4. Scheduler
# ---------------------------------------------------------------------------
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def setup_scheduler(cfg: dict) -> None:
    header("4. Proactive notifications (scheduler)")
    if "bod_time" in cfg and keep_existing(
            f"quiet {cfg.get('quiet_hours_start', 23)}:00-{cfg.get('quiet_hours_end', 7)}:00, "
            f"max {cfg.get('max_notifications_per_day', 8)}/day, BOD {cfg['bod_time']}, "
            f"EOD {cfg.get('eod_time', '22:00')}"):
        return
    if not ask_yes("Configure the background scheduler?"):
        return
    print(f"{DIM}Hours are 0-23. Defaults match what the scheduler uses if unset.{RESET}")
    cfg["quiet_hours_start"] = ask_int("Quiet hours start (hour)", cfg.get("quiet_hours_start", 23), 0, 23)
    cfg["quiet_hours_end"] = ask_int("Quiet hours end (hour)", cfg.get("quiet_hours_end", 7), 0, 23)
    cfg["max_notifications_per_day"] = ask_int("Max notifications per day",
                                               cfg.get("max_notifications_per_day", 8), 0, 50)
    cfg["bod_time"] = ask("Beginning-of-day prompt time", cfg.get("bod_time", "08:30"), valid_time)
    cfg["eod_time"] = ask("End-of-day prompt time", cfg.get("eod_time", "22:00"), valid_time)

    cur = ",".join(cfg.get("checkin_times", ["09:30", "13:00", "16:30"]))
    raw = ask("Check-in times (comma-separated, blank for none)", cur,
              lambda s: None if not s else next((e for e in (valid_time(t.strip()) for t in s.split(",")) if e), None))
    cfg["checkin_times"] = [t.strip() for t in raw.split(",") if t.strip()]

    day_ok = lambda s: None if s in DAYS else f"One of: {', '.join(DAYS)}"
    cfg["weekly_review_day"] = ask("Weekly review day", cfg.get("weekly_review_day", "Sunday"), day_ok)
    cfg["weekly_review_time"] = ask("Weekly review time", cfg.get("weekly_review_time", "18:00"), valid_time)
    save_config(cfg)
    ok("Scheduler settings saved.")


# ---------------------------------------------------------------------------
# 5. Optional integrations (existing per-service scripts do the verifying)
# ---------------------------------------------------------------------------
def setup_extras(cfg: dict) -> None:
    header("5. Optional: Todoist and travel times")
    py = str(VENV_PY) if VENV_PY.exists() else None
    for name, script, what, key in [
        ("Todoist", "setup-todoist.py", "task management via Todoist", "todoist_api_token"),
        ("Travel", "setup-travel.py", "travel-time estimates (Google Routes API, needs billing)",
         "google_maps_api_key"),
    ]:
        if cfg.get(key):
            if keep_existing(f"{name} (key …{cfg[key][-4:]})"):
                continue
        elif not ask_yes(f"Set up {name} — {what}?", False):
            continue
        if not py:
            warn(f"Needs the venv — run {SETUP_CMD} first.")
            continue
        subprocess.run([py, str(ROOT / "setup" / script)])
    # Those scripts write config.json themselves; pick up their changes.
    cfg.update(load_json(CONFIG_PATH))


# ---------------------------------------------------------------------------
# 6. Tool danger levels
# ---------------------------------------------------------------------------
# 0 = read-only (silent), 1 = internal write (one line, confirm only if the
# model flags it), 2 = external/irreversible (always confirm). Anything not
# listed falls back to a name-based guess, and tool_diagnostics treats
# missing tools as 2.
LEVEL_OVERRIDES = {
    "send_email": 2, "send_draft": 2, "trash_email": 2, "delete_calendar": 2,
    "draft_email": 1, "star_email": 1, "archive_email": 1, "mark_email_read": 1,
    "create_email_label": 1,
}
READ_PREFIXES = ("list_", "get_", "read_", "search_", "find_", "check_", "return_", "estimate_")


def tool_names() -> list[str]:
    text = (SRC / "tools.py").read_text(encoding="utf-8")
    return sorted(set(re.findall(r'\{"name": "([a-z_]+)"', text)))


def default_level(name: str) -> int:
    if name in LEVEL_OVERRIDES:
        return LEVEL_OVERRIDES[name]
    return 0 if name.startswith(READ_PREFIXES) else 1


def setup_tool_config() -> None:
    header("6. Tool confirmation levels")
    existing = load_json(TOOL_CONFIG)
    levels = {k: int(v) for k, v in existing.get("danger_levels", {}).items()}
    new = [n for n in tool_names() if n not in levels]
    if not new:
        ok(f"tool_config.json already covers all {len(levels)} tools.")
        return
    proposed = {n: default_level(n) for n in new}
    for lvl, label in [(0, "read-only, run silently"), (1, "internal write, show a line"),
                       (2, "external, ALWAYS confirm")]:
        names = [n for n, l in proposed.items() if l == lvl]
        if names:
            print(f"\n{BOLD}L{lvl} — {label}{RESET} ({len(names)})")
            print("  " + ", ".join(names))
    print(f"\n{DIM}Change any later in the REPL with /danger <tool> <0|1|2>.{RESET}")
    if not ask_yes(f"Write these {len(new)} levels to tool_config.json?"):
        warn("Skipped — unlisted tools will ask for confirmation every time.")
        return
    levels.update(proposed)
    existing["danger_levels"] = dict(sorted(levels.items()))
    existing.setdefault("session_allowlist", [])
    TOOL_CONFIG.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    ok(f"Saved {TOOL_CONFIG.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# 7. Savvy rules
# ---------------------------------------------------------------------------
def setup_rules() -> None:
    header("7. Behavior rules (notes/savvy_rules.md)")
    RULES_PATH.parent.mkdir(exist_ok=True)
    count = sum(1 for l in RULES_PATH.read_text(encoding="utf-8").splitlines() if l.startswith("- ")) \
        if RULES_PATH.exists() else 0
    if count and keep_existing(f"{count} rule(s) in place"):
        return
    print("Standing rules Savvy should always follow, e.g.")
    print(f"{DIM}  Don't book over my therapy appointment on Tuesdays.{RESET}")
    added = []
    while True:
        rule = input("Rule (blank when done): ").strip().lstrip("- ").strip()
        if not rule:
            break
        added.append(rule)
    if added:
        prefix = "\n" if RULES_PATH.exists() and RULES_PATH.stat().st_size else ""
        with open(RULES_PATH, "a", encoding="utf-8") as f:
            f.write(prefix + "".join(f"- {r}\n" for r in added))
        ok(f"Added {len(added)} rule(s).")


# ---------------------------------------------------------------------------
def summary(cfg: dict) -> None:
    header("Summary")
    rows = [
        ("Anthropic key", bool(cfg.get("anthropic_api_key"))),
        ("Google client (credentials.json)", GOOGLE_CLIENT.exists()),
        ("Google accounts", ", ".join(existing_labels()) or False),
        ("Signal numbers", bool(cfg.get("sender_number") and cfg.get("recipient_number"))),
        ("Todoist", bool(cfg.get("todoist_api_token"))),
        ("Travel (Routes API)", bool(cfg.get("google_maps_api_key"))),
        ("Tool levels (tool_config.json)", TOOL_CONFIG.exists()),
        ("Python venv (.venv/)", VENV_PY.exists()),
    ]
    for name, val in rows:
        mark = f"{GREEN}✓{RESET}" if val else f"{DIM}–{RESET}"
        extra = f"  {val}" if isinstance(val, str) else ""
        print(f"  {mark} {name}{extra}")
    print(f"\nStart Savvy with:  {LAUNCH_CMD}")
    print("Personalize the system prompt in src/prompt.py (see setup.md §7).")


def main() -> int:
    if os.name == "nt":
        os.system("")   # turns on ANSI colors in the Windows console
    print(f"{BOLD}Savvy configuration wizard{RESET}  —  writes to {CRED_DIR}/")
    print(f"{DIM}Press Enter to accept [defaults]. Ctrl+C quits; finished sections stay saved.{RESET}")
    if not VENV_PY.exists():
        warn("No .venv/ yet. Google authorization and Todoist/Travel checks need it — "
             f"run {SETUP_CMD} first if you want those.")
    cfg = load_json(CONFIG_PATH)
    try:
        setup_anthropic(cfg)
        setup_google(cfg)
        setup_signal(cfg)
        setup_scheduler(cfg)
        setup_extras(cfg)
        setup_tool_config()
        setup_rules()
    except (KeyboardInterrupt, EOFError):
        print("\n\nStopped. Everything entered in finished sections was saved.")
        return 130
    summary(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())

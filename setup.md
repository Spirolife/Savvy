# Setup Guide

This walks through getting Savvy running end-to-end: Python, local embeddings, Google APIs, Signal, and the optional background scheduler. It also covers personalizing the system prompt, which is the part that makes Savvy actually useful for *you* rather than a generic assistant.

If you hit issues, the [Troubleshooting](#troubleshooting) section at the bottom covers the things that have caught me out so far.

## Prerequisites

- Linux, macOS, or Windows 10/11 (see [Windows](#windows) for the PowerShell equivalents of each command)
- For Signal: Podman or Docker (the setup script offers to install Podman if you have neither)
- Python 3.11+
- An Anthropic API key — get one at [console.anthropic.com](https://console.anthropic.com/)
- A Google account (for Calendar/Gmail/Tasks integration — optional but most of the value lives here)
- A Signal account on a phone (for the texting interface — also optional)

## Project Layout

After setup, your tree should look like this:

```
secretary/
├── credentials/
│   ├── config.json            # API keys, model choice, Signal numbers
│   ├── credentials.json       # Google OAuth client (downloaded from Cloud Console)
│   ├── tool_config.json       # Per-tool danger levels for the confirmation gate
│   └── token_<label>.json     # Per-account Google refresh tokens (auto-created)
├── memory/
│   ├── secretary_memory.db    # SQLite — conversation, facts, embeddings
│   ├── scheduler_state.json   # Notification budget tracking (auto-created)
│   └── tool_audit.jsonl       # Append-only log of every tool call (auto-created)
├── notes/
│   └── savvy_rules.md         # Durable behavior rules surfaced to the model
├── logs/
│   ├── ollama.log             # Background process logs (auto-created)
│   ├── signal.log
│   └── scheduler.log
├── setup/
│   ├── setup.sh / setup.ps1   # One-shot installer (Linux+macOS / Windows)
│   ├── configure.py           # Credentials wizard (run by setup)
│   ├── setup-signal.py        # Signal container setup
│   ├── launch.sh / launch.ps1 # Start everything
│   └── stop.sh / stop.ps1     # Stop background services
├── src/
│   ├── secretary.py           # Entry point
│   ├── prompt.py              # System prompts — edit this to personalize
│   └── ...
├── requirements.txt
└── .venv/
```

The `credentials/`, `memory/`, and `logs/` directories are auto-created where needed.

## 1. One-shot Setup

The `setup/setup.sh` script handles Python, Ollama, and the embedding model in one go:

```bash
git clone <your-repo-url> secretary
cd secretary
chmod +x setup/setup.sh
./setup/setup.sh
```

On Windows, run `powershell -ExecutionPolicy Bypass -File setup\setup.ps1` instead (see [Windows](#windows)).

This will:

- Install Ollama if missing
- Start the Ollama server in the background
- Check for an NVIDIA GPU (warns but does not block if absent)
- Pull `mistral:7b-instruct-v0.3-q4_K_M` and `nomic-embed-text` models
- Create a Python virtual environment and install `requirements.txt`
- Run the configuration wizard (`setup/configure.py`), which prompts for everything in `credentials/` — Anthropic key, Google OAuth client and account sign-ins, Signal numbers, scheduler times, Todoist/travel keys, tool confirmation levels — and a few starter rules. Every section can be skipped.

To change settings later, re-run just the wizard: `python3 setup/configure.py`. It shows your current values as defaults, so you only retype what changes. Sections 2–5 below explain what each setting does and how to do it by hand if you prefer.

## 2. Anthropic API Key

Create or edit `credentials/config.json`:

```json
{
  "anthropic_api_key": "sk-ant-...",
  "anthropic_model": "claude-sonnet-4-6",
  "max_tokens": 1024,
  "context_top_k": 8,
  "recent_window": 10
}
```

You can also set `ANTHROPIC_API_KEY` as an environment variable instead, but be aware: if both are present, the env var wins. If you find Savvy using the wrong key, run `unset ANTHROPIC_API_KEY` and rely on the config file.

At this point you can already run Savvy with just the API key — no Google, no Signal, no scheduler:

```bash
./setup/launch.sh
```

The launch script will try to start the Signal bot and the scheduler in the background and they'll error out in their respective logs (because nothing's configured yet), but that won't block the REPL. You should still get a working Secretary prompt in the foreground. Test it with a question, then `/quit`.

Once you've added the Google and Signal integrations below, the background services will actually do something useful.

## 3. Google API Setup

This is the longest part of setup. The good news is you only do it once.

### 3a. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com/)
2. Click the project dropdown at the top, then **New Project**. Name it whatever you like ("savvy", "personal-secretary", etc.)
3. Make sure the new project is selected before continuing

### 3b. Enable the APIs

In the left sidebar, go to **APIs & Services → Library**, then enable each of these:

- Google Calendar API
- Gmail API
- Google Tasks API

(Search for each by name and click Enable.)

### 3c. Configure the OAuth consent screen

1. **APIs & Services → OAuth consent screen**
2. Choose **External** (unless you have a Google Workspace and want Internal)
3. Fill in the required fields — app name, your email, developer email. The rest can stay blank
4. On the Scopes step, click **Save and Continue** without adding any (Savvy requests scopes at runtime)
5. On the Test Users step, **add your own Gmail address as a test user**. This is the account whose calendar and email Savvy will manage. If you want to manage multiple accounts, add all of them here
6. Save

The app will stay in "testing" mode, which is fine for personal use. You don't need to verify the app or publish it.

### 3d. Create OAuth credentials

1. **APIs & Services → Credentials**
2. **Create Credentials → OAuth client ID**
3. Application type: **Desktop app**
4. Name it whatever
5. Click **Create**, then **Download JSON**
6. Save that file as `credentials/credentials.json` in your Savvy project

### 3e. Authorize each Google account

For each account you want Savvy to access, run:

```bash
source .venv/bin/activate
python src/google_auth.py <label>
```

The label is whatever you want to call the account internally — e.g. `personal`, `work`, `school`. A browser window will open; log in with the correct Google account and grant the requested permissions.

Common labels:

```bash
python src/google_auth.py personal
python src/google_auth.py northeastern
```

This creates `credentials/token_<label>.json` for each account. These are the refresh tokens — keep them private.

> **Heads up:** OAuth tokens are tied to *which Google account you authorized*, not to the label you typed. If you accidentally log in with the wrong account during the browser flow, the label will be misleading. Verify with `/calendar` and `/email` after first run; if the wrong calendar shows up, delete the token file and re-authorize.

### 3f. Verify

Start Savvy with the launch script:

```bash
./setup/launch.sh
```

Look for a line like `Google: personal, northeastern` near the top, then run:

```
/calendar
/email
```

You should see today's events and recent emails. If you don't, jump to Troubleshooting.

## 4. Signal Bot (Optional)

The Signal bot uses the **signal-cli-rest-api container**, not signal-cli directly, so nothing needs installing beyond a container runtime. The script uses Podman if present, otherwise Docker, and works on immutable distros like SecureBlue / Fedora Kinoite as well as regular ones like Ubuntu/Kubuntu.

### 4a. Run the Signal setup script

The configuration wizard runs this for you. To run it by hand:

```bash
python3 setup/setup-signal.py
```

This will:

- Find Podman or Docker (offering to install Podman via apt/dnf/pacman/zypper, Homebrew, or winget if neither is present; on macOS/Windows it also creates and starts the Podman VM)
- Pull and start the `bbernhard/signal-cli-rest-api` container on `localhost:8080` (bound to localhost only, so other machines on your network can't send messages as you)
- Print instructions for linking it to your Signal account

### 4b. Give Savvy a Signal identity

The configuration wizard (`setup/configure.py`) does this step for you and asks which of two setups you want:

**Option 1 — its own number (recommended).** Savvy gets a separate Signal account and shows up as its own contact you chat with. You need a second number that can receive an SMS or voice call (spare SIM, Google Voice, a landline). Don't use the number your phone's Signal already uses: registering it in the container logs your phone out.

By hand:

```bash
# 1. Solve https://signalcaptchas.org/registration/generate.html, right-click
#    "Open Signal" → copy link, and drop the signalcaptcha:// prefix
curl -X POST -H 'Content-Type: application/json' \
     -d '{"captcha": "<token>"}' http://localhost:8080/v1/register/+15559990000
# 2. Enter the SMS code (add '{"use_voice": true}' to step 1 for a call instead,
#    after an SMS attempt and a ~60s wait)
curl -X POST -H 'Content-Type: application/json' -d '{}' \
     http://localhost:8080/v1/register/+15559990000/verify/123456
# 3. Optional display name
curl -X PUT -H 'Content-Type: application/json' -d '{"name": "Savvy"}' \
     http://localhost:8080/v1/profiles/+15559990000
```

Then text the new number from your phone once so the chat exists.

**Option 2 — link to your own account.** The container joins your account as a linked device (like Signal Desktop), and you talk to Savvy in **Note to Self**. No second number needed:

1. Open `http://localhost:8080/v1/qrcodelink?device_name=savvy` in a browser
2. On your phone: Signal → Settings → Linked Devices → Link New Device
3. Scan the QR code

### 4c. Configure phone numbers

The wizard writes these to `credentials/config.json`:

```json
{
  "signal_api_url": "http://localhost:8080",
  "sender_number": "+15559990000",
  "recipient_number": "+15551234567"
}
```

- `sender_number` is the account Savvy sends from: the bot's own number (option 1) or your number (option 2)
- `recipient_number` is the only number Savvy will respond to: your personal number. **Set this.** Without it, anyone who knows the bot's number could text it. It can also be a list of numbers

With option 2, both numbers are the same.

### 4d. Optional: auto-start the container on login

**Docker:** nothing to do — the container is created with `--restart unless-stopped`.

**Podman:**

```bash
mkdir -p ~/.config/systemd/user
podman generate systemd --name signal-api --new \
    > ~/.config/systemd/user/container-signal-api.service
systemctl --user daemon-reload
systemctl --user enable container-signal-api.service
```

## 5. Background Notification Scheduler (Optional)

The scheduler is a separate process that wakes up periodically, checks your calendar/diary/recent activity, and decides whether to send a proactive Signal notification. It enforces quiet hours and a daily budget so it stays useful.

### 5a. Configure

Add to `credentials/config.json`:

```json
{
  "max_notifications_per_day": 5,
  "quiet_hours_start": 22,
  "quiet_hours_end": 8
}
```

### 5b. Optional: install as a user systemd service

The repo includes `memory/secretary-scheduler.service`. Copy it into your user systemd config and enable it:

```bash
cp memory/secretary-scheduler.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now secretary-scheduler.service
```

Check it's running:

```bash
systemctl --user status secretary-scheduler.service
journalctl --user -u secretary-scheduler.service -f
```

If you'd rather just have `launch.sh` start it as a regular background process, you can skip the systemd step entirely.

## 6. Running Savvy

### Desktop app (recommended)

Savvy lives in the system tray, like Discord. `setup.sh` offers to start it at the end; after that it starts on login, and you can open it from your app launcher ("Savvy") or with:

```bash
.venv/bin/python src/desktop.py
```

The tray app:

- **Runs the Signal bot and scheduler** for you and restarts them if they crash (you get a desktop notification when that happens). It also starts Ollama if it's installed but not running.
- **Shows health in the icon:** green = all good, yellow = running but Ollama is off (memory search falls back to recent messages), red = something Savvy needs is down, grey = paused. Hover for a summary; the menu lists each part.
- **Opens a chat window** when you click the icon. It's the same Savvy as the terminal and Signal, with the same memory, and shows recent messages from both. Tools that need confirmation pop up an Approve / Deny / Always-allow dialog.
- **Menu:** pause/start or restart services, open the logs folder, toggle **Start on login**, quit.

Closing the chat window just hides it; **Quit Savvy** in the tray menu stops everything. Launching it a second time opens the window of the copy that's already running.

The terminal REPL still works alongside it: `./setup/launch.sh` notices the tray app and only opens the REPL, leaving the services to it.

### Terminal only

Without the desktop app:

```bash
./setup/launch.sh
```

This starts:

- Ollama (if not already running) — backgrounded, log at `logs/ollama.log`
- Signal bot — backgrounded, log at `logs/signal.log`
- Scheduler — backgrounded, log at `logs/scheduler.log`
- Secretary REPL — foreground, in your terminal

To stop everything:

```bash
./setup/stop.sh
```

This kills the Signal bot and scheduler. It leaves Ollama running on purpose; if you want to stop that too, `pkill ollama`.

To watch logs live:

```bash
tail -f logs/signal.log
tail -f logs/scheduler.log
```

## 7. Personalizing the System Prompt

This is what turns Savvy from a generic assistant into one that knows your situation, your goals, and how you want to be talked to. There are two places to do it:

### 7a. The system prompt — `src/prompt.py`

The prompt variables in `src/prompt.py`:

- `SYSTEM_PROMPT` — the one prompt every surface uses: the desktop REPL, the Signal bot, and scheduled check-ins. This is the one to personalize.
- `SURFACE_STYLE` — a short reply-style block added per surface. Signal's asks for short, plain-text replies; that is the only difference between Signal and the REPL.
- `CHECKIN_INSTRUCTION` — what a scheduled check-in is asked to do (look for updates to ask about, upcoming events, vague blocks).
- `FACT_EXTRACTION_PROMPT` — used internally by the fact extractor. You probably don't need to touch this one.

The default prompt is just a placeholder. The more specific you make it, the better Savvy gets. Things worth including:

- **Your name and what you do.** "I'm a grad student in robotics," "I work in finance," etc.
- **What you want help with.** Specific recurring problems — "I lose track of personal projects when work gets busy," "I forget to follow up on emails," "I want help sticking to a workout schedule."
- **Goals.** Year-long resolutions, fitness targets, habits you're trying to build. Savvy will reference these when relevant — for example, noticing if your diary shows you skipped a planned workout.
- **Communication style.** Whether you want pushback or agreement, formal or casual tone, short or detailed responses. The dev set hers up to question her decisions a little bit, especially when skipping things related to her end of year goals. 
- **Any non-negotiables.** Things Savvy should never do — for example, "don't suggest medical advice," "don't book over my standing therapy appointment on Tuesdays."
- **Timezone.** The default is `America/New_York`. Change it if needed.

The prompt template has placeholders (`{datetime}`, `{calendar_context}`, `{email_context}`, `{diary_context}`) that get filled in at runtime. Don't remove these — they're how Savvy gets your live calendar and email into the prompt each turn. Just edit the prose around them.

### 7b. Behavior rules — `notes/savvy_rules.md`

Day-to-day rules and preferences that change over time live in `notes/savvy_rules.md`. This file gets surfaced to the model and is meant for the kind of stuff you'd otherwise have to keep re-explaining:

```markdown
- Only use the work calendar for events; ignore the personal calendar with old data.
- Laundry takes 3-4 hours and I need to be home for it — schedule passive things during it.
- Don't book over my standing therapy appointment on Tuesdays.
- When no reminder time is given, prefer passive/waiting time (laundry, commute) over active appointments.
```

I find this is the file I edit most often, because the system prompt covers identity and goals while this file covers operational quirks. You can ask Savvy to add things to this,
but be sure to check it afterward in case it hallucinates actually calling the tool to save it. For example, "From now on, please schedule quick things like calls during when I'm doing laundry on Wednesdays unless otherwise specified."

### 7c. Privacy note about prompts

The system prompt and any rules surfaced into context go to the Anthropic API on every turn. So anything you put in there — health conditions, fitness goals, work details — will be sent. This is fine if you're comfortable with Anthropic's data handling (30-day retention, no training), but if you have especially sensitive information, consider keeping it in the local fact store instead. The fact extractor will pull facts from conversation into a local DB, and only the relevant ones get retrieved into context per turn. That gives you finer control over what gets transmitted.

## Windows

Everything works on Windows 10/11, including the tray app (section 6). The optional systemd pieces (the scheduler service in 5b, Signal autostart in 4d) are Linux-only; the tray app covers the same ground. Use these instead of the shell commands in this guide:

| Linux / macOS | Windows (PowerShell) |
|---|---|
| `./setup/setup.sh` | `powershell -ExecutionPolicy Bypass -File setup\setup.ps1` |
| `.venv/bin/python src/desktop.py` | Start Menu → **Savvy** (added by `setup.ps1`) |
| `./setup/launch.sh` | `powershell -ExecutionPolicy Bypass -File setup\launch.ps1` |
| `./setup/stop.sh` | `powershell -ExecutionPolicy Bypass -File setup\stop.ps1` |
| `source .venv/bin/activate` | `.venv\Scripts\Activate.ps1` |
| `python3 setup/configure.py` | `.venv\Scripts\python setup\configure.py` |
| `tail -f logs/signal.log` | `Get-Content -Wait logs\signal.err.log` |
| `pgrep -af scheduler.py` | `Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" \| Select CommandLine` |

Notes:

- **Prerequisites.** `setup.ps1` installs Ollama with `winget` if it's missing. It needs Python 3.11+ already installed (`winget install -e --id Python.Python.3.12`, then open a new PowerShell window).
- **Signal.** The easiest route is Podman: `setup-signal.py` installs it with `winget` and creates its Linux VM, which needs WSL2 (`wsl --install` as admin, then reboot). Docker Desktop works too if you already have it. After a reboot, run `podman machine start; podman start signal-api` (Docker Desktop restarts the container itself).
- **Logs.** On Windows, background services write stdout to `logs\<name>.log` and stderr (where Python logging goes) to `logs\<name>.err.log`.
- **Running Python by hand.** Set `$env:PYTHONUTF8 = "1"` first, or files with emoji and other non-ASCII text fail to read. The `.ps1` scripts set it for you.
- **Start on login.** The tray app adds itself to your login items (HKCU Run key) on first launch; turn it off with **Start on login** in its menu.

## Troubleshooting

### `ModuleNotFoundError` on a fresh setup

Make sure the venv is activated and you've run `pip install -r requirements.txt` inside it.

### Savvy is using the wrong API key

The `ANTHROPIC_API_KEY` environment variable overrides `credentials/config.json`. If you've recently rotated keys, your shell may have a stale value. Run:

```bash
unset ANTHROPIC_API_KEY
```

Then start Savvy again.

### Google OAuth flow opens browser but errors out

Most common cause: the Google account you're logging in with isn't on the test user list. Add it under **OAuth consent screen → Test users** in the Cloud Console.

Second most common: the `credentials/credentials.json` file doesn't match the project where you enabled the APIs. Re-download from the Credentials page and replace.

### `/calendar` shows the wrong account's events

The token labels in `credentials/token_<label>.json` don't enforce *which* account they're attached to — that's determined by which Google account you logged into during the browser flow. List the connected accounts:

```bash
python src/google_auth.py
```

If the labels are wrong, delete the misnamed token files and re-run `python src/google_auth.py <correct_label>`.

### Multi-line paste in the REPL only captures the first line

Fixed in current versions, but if you hit it: the input handler uses `select` to detect pasted content. If your terminal is unusual, try piping input via stdin instead.

### Signal bot doesn't reply

Check in order:

1. The container is running: `podman ps | grep signal-api` (or `docker ps`)
2. The API responds: `curl http://localhost:8080/v1/about`
3. The bot is actually receiving messages: `tail -f logs/signal.log` and look for `In: ...` lines
4. `recipient_number` matches the number you're texting *from*, with country code (`+1...`)
5. The Signal account is still linked: `curl http://localhost:8080/v1/accounts` should list your number

### Ollama embeddings aren't working

If you see "Ollama: off (recency-only memory)" at startup, Savvy can't reach `http://localhost:11434`. Either Ollama isn't running, or it's bound to a different port. Start it with `ollama serve` and confirm with `curl http://localhost:11434/api/tags`.

Without Ollama, Savvy can only retrieve messages from the recent_window (default last 10). With Ollama, it also surfaces semantically related messages from anywhere in your history — so referencing something from weeks ago still works.

### Scheduler isn't firing notifications

Check:

1. The process is running: `pgrep -af scheduler.py`
2. You're not in quiet hours — adjust `quiet_hours_start` / `quiet_hours_end` if needed
3. Daily budget isn't already spent — check `memory/scheduler_state.json`
4. Look at `logs/scheduler.log` for errors
5. Test the notifier directly: `python -c "from src.notifier import send_message, load_config; send_message('test', load_config())"`
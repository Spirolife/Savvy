# Setup Guide

This walks through getting Savvy running end-to-end: Python, local embeddings, Google APIs, Signal, and the optional background scheduler. It also covers personalizing the system prompt, which is the part that makes Savvy actually useful for *you* rather than a generic assistant.

If you hit issues, the [Troubleshooting](#troubleshooting) section at the bottom covers the things that have caught me out so far.

## Prerequisites

- Linux or macOS (developed on Fedora-derived SecureBlue; the Signal flow assumes Podman is available, which it is by default on Fedora Kinoite / SecureBlue)
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
│   ├── setup.sh               # One-shot installer
│   ├── setup-signal.sh        # Signal container setup
│   ├── launch.sh              # Start everything
│   └── stop.sh                # Stop background services
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

This will:

- Install Ollama if missing
- Start the Ollama server in the background
- Check for an NVIDIA GPU (warns but does not block if absent)
- Pull `mistral:7b-instruct-v0.3-q4_K_M` and `nomic-embed-text` models
- Create a Python virtual environment and install `requirements.txt`

> **Heads up: hardcoded path.** `launch.sh` assumes the project lives at `$HOME/Documents/Projects/secretary`. If you cloned it elsewhere, edit the `DIR=` line at the top of `launch.sh`.

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

The Signal bot uses a **signal-cli-rest-api Podman container**, not signal-cli directly. This makes it easy to run on immutable distros like SecureBlue / Fedora Kinoite where Podman is preinstalled.

### 4a. Run the Signal setup script

```bash
chmod +x setup/setup-signal.sh
./setup/setup-signal.sh
```

This will:

- Confirm Podman is available
- Pull and start the `bbernhard/signal-cli-rest-api` container on port 8080
- Print instructions for linking it to your Signal account

### 4b. Link to your Signal account

After the container is running, the script will tell you to:

1. Open `http://localhost:8080/v1/qrcodelink?device_name=secretary` in a browser
2. On your phone: Signal → Settings → Linked Devices → Link New Device
3. Scan the QR code

Once linked, get your phone number from:

```bash
curl -s http://localhost:8080/v1/accounts
```

### 4c. Configure phone numbers

Add to `credentials/config.json`:

```json
{
  "signal_api_url": "http://localhost:8080",
  "sender_number": "+15551234567",
  "recipient_number": "+15551234567"
}
```

- `sender_number` is the Signal-linked number from the previous step
- `recipient_number` is the only number Savvy will respond to. **Set this**. Without it, anyone who knows your linked Signal number could text the bot

For most personal setups, both numbers are the same — you're texting yourself.

### 4d. Optional: auto-start the container on login

```bash
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

Once everything above is set up, the normal day-to-day is just:

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

There are three prompt variables in `src/prompt.py`:

- `SYSTEM_PROMPT` — used by the desktop REPL. This is the main one.
- `SIGNAL_SYSTEM_PROMPT` — used by the Signal bot. Tuned to be more concise and SMS-friendly.
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

## Troubleshooting

### `ModuleNotFoundError` on a fresh setup

Make sure the venv is activated and you've run `pip install -r requirements.txt` inside it.

### `launch.sh` fails immediately

Most common cause: the hardcoded `DIR=` path at the top of `launch.sh` doesn't match where you cloned the project. Edit it to match.

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

1. The container is running: `podman ps | grep signal-api`
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
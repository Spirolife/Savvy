#!/usr/bin/env bash
# launch.sh — Start Savvy
#
# Ollama, Signal bot, and Scheduler run in the background.
# Only the Secretary REPL opens a window.

DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$DIR/src"
ACT="source $DIR/.venv/bin/activate"
export PATH="$HOME/.local/bin:$PATH"
LOGDIR="$DIR/logs"
mkdir -p "$LOGDIR"

# The tray app (src/desktop.py) supervises the bot and scheduler itself;
# starting a second set here would mean two bots answering every message.
if pgrep -f "^[^ ]*python[0-9.]* [^ ]*desktop\.py" > /dev/null; then
    echo "[✓] Savvy desktop is running and managing the background services."
    echo "Opening Secretary..."
    cd "$SRC" && $ACT && python secretary.py
    exit
fi

# A previous launch's bot/scheduler would keep running alongside the new
# ones — two bots polling the same account, and duplicate notifications.
"$DIR/setup/stop.sh" > /dev/null

echo "Starting background services..."

# Ollama (skip if already running)
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    ollama serve > "$LOGDIR/ollama.log" 2>&1 &
    echo "[✓] Ollama started"
    sleep 2
else
    echo "[✓] Ollama already running"
fi

# Signal bot
(cd $SRC && $ACT && python secretary.py --signal > "$LOGDIR/signal.log" 2>&1) &
echo "[✓] Signal bot started (log: logs/signal.log)"

# Scheduler
(cd $SRC && $ACT && python scheduler.py > "$LOGDIR/scheduler.log" 2>&1) &
echo "[✓] Scheduler started (log: logs/scheduler.log)"

sleep 1
echo ""
echo "Opening Secretary..."
echo "  Logs: tail -f $LOGDIR/signal.log"
echo "        tail -f $LOGDIR/scheduler.log"
echo ""

# Secretary REPL — foreground
cd $SRC && $ACT && python secretary.py
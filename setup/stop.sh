#!/usr/bin/env bash
# stop.sh — Stop all background secretary services
# Patterns are anchored to a python interpreter so they can't match, say, an
# editor that has scheduler.py open.
echo "Stopping secretary services..."
pkill -f "^[^ ]*python[0-9.]* (-u )?secretary\.py --signal" 2>/dev/null && echo "[✓] Signal bot stopped" || echo "[•] Signal bot not running"
pkill -f "^[^ ]*python[0-9.]* (-u )?scheduler\.py" 2>/dev/null && echo "[✓] Scheduler stopped" || echo "[•] Scheduler not running"
echo "Done. (Ollama left running — stop with: pkill ollama)"

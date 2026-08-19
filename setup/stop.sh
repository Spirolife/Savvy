#!/usr/bin/env bash
# stop.sh — Stop all background secretary services
echo "Stopping secretary services..."
pkill -f "secretary.py --signal" 2>/dev/null && echo "[✓] Signal bot stopped" || echo "[•] Signal bot not running"
pkill -f "scheduler.py" 2>/dev/null && echo "[✓] Scheduler stopped" || echo "[•] Scheduler not running"
echo "Done. (Ollama left running — stop with: pkill ollama)"
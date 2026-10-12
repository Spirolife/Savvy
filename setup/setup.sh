#!/usr/bin/env bash
# setup.sh — One-shot setup for the local private secretary
# Run: chmod +x setup.sh && ./setup.sh

set -e

# Always work from the project root, wherever this was launched from.
cd "$(dirname "$0")/.."

echo "========================================="
echo "  Local Private Secretary — Setup"
echo "========================================="
echo ""

# -----------------------------------------------
# 1. Check/install Ollama
# -----------------------------------------------
if command -v ollama &> /dev/null; then
    echo "[✓] Ollama is already installed: $(ollama --version)"
else
    echo "[•] Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    echo "[✓] Ollama installed"
fi

# -----------------------------------------------
# 2. Start Ollama server if not running
# -----------------------------------------------
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "[✓] Ollama server is running"
else
    echo "[•] Starting Ollama server in the background..."
    ollama serve &> /tmp/ollama.log &
    sleep 3
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "[✓] Ollama server started"
    else
        echo "[!] Could not start Ollama. Check /tmp/ollama.log"
        echo "    You may need to run 'ollama serve' manually."
    fi
fi

# -----------------------------------------------
# 3. Check NVIDIA GPU setup
# -----------------------------------------------
echo ""
echo "--- GPU Check ---"
if command -v nvidia-smi &> /dev/null; then
    echo "[✓] NVIDIA driver found"
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
else
    echo "[!] nvidia-smi not found. GPU acceleration may not work."
    echo "    Install NVIDIA drivers: sudo apt install nvidia-driver-535"
    echo "    (Continuing with CPU-only for now)"
fi
echo ""

# -----------------------------------------------
# 4. Pull models
# -----------------------------------------------
echo "--- Pulling Models ---"
echo "[•] Pulling Mistral 7B Instruct (Q4_K_M) — ~4.4 GB download..."
ollama pull mistral:7b-instruct-v0.3-q4_K_M

echo "[•] Pulling nomic-embed-text — ~270 MB download..."
ollama pull nomic-embed-text

echo "[✓] Models ready"
echo ""

# -----------------------------------------------
# 5. Python dependencies
# -----------------------------------------------
echo "--- Python Setup ---"
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo "[!] Python not found. Install python3 first."
    exit 1
fi

echo "[•] Python: $($PYTHON --version)"

# Create venv if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "[•] Creating virtual environment..."
    $PYTHON -m venv .venv
fi

source .venv/bin/activate
echo "[•] Installing Python dependencies..."
pip install -q -r requirements.txt
echo "[✓] Python dependencies installed"

echo ""

# -----------------------------------------------
# 6. Credentials & config (interactive)
# -----------------------------------------------
# Writes credentials/config.json, credentials.json, token_<label>.json,
# tool_config.json and notes/savvy_rules.md. Safe to re-run on its own:
#   python3 setup/configure.py
python setup/configure.py

echo ""
echo "========================================="
echo "  Setup complete!"
echo "========================================="
echo ""
echo "Savvy runs from the system tray: it starts on login, and you can open it"
echo "any time from your app launcher (\"Savvy\")."
echo "Terminal REPL instead:          ./setup/launch.sh"
echo "To change any settings later:  python3 setup/configure.py"
echo ""

# -----------------------------------------------
# 7. Start the tray app (detached, so closing this terminal doesn't stop it)
# -----------------------------------------------
read -r -p "Start Savvy in the system tray now? [Y/n] " ans
if [[ ! "$ans" =~ ^[Nn] ]]; then
    nohup .venv/bin/python src/desktop.py > /dev/null 2>&1 &
    disown
    echo "[✓] Savvy is starting — look for its icon in the system tray."
fi

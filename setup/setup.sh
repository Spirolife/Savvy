#!/usr/bin/env bash
# setup.sh — One-shot setup for the local private secretary
# Run: chmod +x setup.sh && ./setup.sh

set -e

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
echo "========================================="
echo "  Setup complete!"
echo "========================================="
echo ""
echo "To run the secretary:"
echo "  source .venv/bin/activate"
echo "  python secretary.py"
echo ""
echo "Your data will be stored in: secretary_memory.db"
echo "Nothing is sent to any external server."
echo ""
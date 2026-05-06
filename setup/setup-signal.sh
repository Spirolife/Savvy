#!/usr/bin/env bash
# setup-signal.sh — Set up the Signal notification container
# For SecureBlue / Fedora Kinoite (uses Podman, which is pre-installed)

set -e

echo "========================================="
echo "  Signal Notification Setup"
echo "========================================="
echo ""

# -----------------------------------------------
# 1. Check Podman
# -----------------------------------------------
if command -v podman &> /dev/null; then
    echo "[✓] Podman found: $(podman --version)"
else
    echo "[!] Podman not found. On SecureBlue/Kinoite it should be pre-installed."
    echo "    Try: rpm-ostree install podman"
    exit 1
fi

# -----------------------------------------------
# 2. Start signal-cli-rest-api container
# -----------------------------------------------
if podman ps --format '{{.Names}}' | grep -q '^signal-api$'; then
    echo "[✓] signal-api container is already running"
else
    if podman ps -a --format '{{.Names}}' | grep -q '^signal-api$'; then
        echo "[•] Starting existing signal-api container..."
        podman start signal-api
    else
        echo "[•] Pulling and starting signal-cli-rest-api container..."
        podman run -d --name signal-api \
            -p 8080:8080 \
            -v signal-cli-data:/home/.local/share/signal-cli \
            -e MODE=native \
            bbernhard/signal-cli-rest-api
    fi

    # Wait for it to be ready
    echo "[•] Waiting for container to start..."
    sleep 5

    if podman ps --format '{{.Names}}' | grep -q '^signal-api$'; then
        echo "[✓] signal-api container is running"
    else
        echo "[!] Container failed to start. Check: podman logs signal-api"
        exit 1
    fi
fi

# -----------------------------------------------
# 3. Check API health
# -----------------------------------------------
echo ""
if curl -s http://localhost:8080/v1/about > /dev/null 2>&1; then
    echo "[✓] Signal API is responding"
else
    echo "[!] Signal API not responding yet. It may need a moment."
    echo "    Check: curl http://localhost:8080/v1/about"
fi

# -----------------------------------------------
# 4. Link to Signal account
# -----------------------------------------------
echo ""
echo "========================================="
echo "  Link to your Signal account"
echo "========================================="
echo ""
echo "1. Open this URL in your browser:"
echo ""
echo "   http://localhost:8080/v1/qrcodelink?device_name=secretary"
echo ""
echo "2. On your phone, open Signal:"
echo "   Settings > Linked Devices > Link New Device"
echo ""
echo "3. Scan the QR code shown in the browser."
echo ""
echo "4. After linking, get your phone number from:"
echo "   curl -s http://localhost:8080/v1/accounts"
echo ""
echo "5. Edit config.json and set both sender_number and"
echo "   recipient_number to your phone number."
echo ""
echo "6. Test it:"
echo "   python notifier.py"
echo ""

# -----------------------------------------------
# 5. Auto-start on boot (optional)
# -----------------------------------------------
echo "========================================="
echo "  Optional: Auto-start on boot"
echo "========================================="
echo ""
echo "To start the Signal container automatically on login:"
echo ""
echo "  podman generate systemd --name signal-api --new > \\"
echo "      ~/.config/systemd/user/container-signal-api.service"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user enable container-signal-api.service"
echo ""
echo "To also auto-start the scheduler:"
echo ""
echo "  cp secretary-scheduler.service ~/.config/systemd/user/"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user enable --now secretary-scheduler.service"
echo ""
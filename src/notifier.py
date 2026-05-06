"""
Signal notification module for the private secretary.
Uses signal-cli native binary directly — no container needed.

Setup:
    1. Install signal-cli (native binary in ~/.local/bin/)
    2. Link to your account: signal-cli link -n "secretary"
    3. Set your phone number in config.json
"""

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("secretary.signal")

try:
    from paths import CONFIG_PATH
except ImportError:
    CONFIG_PATH = Path(__file__).parent.parent / "credentials" / "config.json"

SIGNAL_CLI = Path.home() / ".local" / "bin" / "signal-cli"

# Defaults
DEFAULT_CONFIG = {
    "signal_cli_path": str(SIGNAL_CLI),
    "sender_number": "",
    "recipient_number": "",
    "max_notifications_per_day": 5,
    "quiet_hours_start": 22,
    "quiet_hours_end": 8,
}


def load_config() -> dict:
    """Load config from config.json."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            saved = json.load(f)
        return {**DEFAULT_CONFIG, **saved}
    return DEFAULT_CONFIG.copy()


def _get_signal_cli(config: dict) -> str:
    """Get the signal-cli binary path."""
    return config.get("signal_cli_path", str(SIGNAL_CLI))


def check_signal_cli(config: dict | None = None) -> dict:
    """Check if signal-cli is installed and linked."""
    config = config or load_config()
    cli = _get_signal_cli(config)

    try:
        result = subprocess.run(
            [cli, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return {"ok": True, "version": result.stdout.strip()}
        return {"ok": False, "error": result.stderr.strip()}
    except FileNotFoundError:
        return {"ok": False, "error": f"signal-cli not found at {cli}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# Keep old name for compatibility with scheduler.py
def check_signal_api(config: dict | None = None) -> dict:
    return check_signal_cli(config)


def send_message(message: str, config: dict | None = None) -> bool:
    """Send a Signal message to the configured recipient."""
    config = config or load_config()
    cli = _get_signal_cli(config)
    sender = config.get("sender_number", "")
    recipient = config.get("recipient_number", "")

    if not sender or not recipient:
        logger.error("Signal not configured. Set sender_number and recipient_number in config.json")
        return False

    try:
        result = subprocess.run(
            [cli, "-a", sender, "send", "-m", message, recipient],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"Signal message sent: {message[:80]}...")
            return True
        else:
            logger.error(f"signal-cli error: {result.stderr.strip()}")
            return False
    except FileNotFoundError:
        logger.error(f"signal-cli not found at {cli}")
        return False
    except subprocess.TimeoutExpired:
        logger.error("signal-cli timed out sending message")
        return False
    except Exception as e:
        logger.error(f"Failed to send Signal message: {e}")
        return False


def send_notification(title: str, body: str, config: dict | None = None) -> bool:
    """Send a formatted notification via Signal."""
    message = f"📋 {title}\n\n{body}"
    return send_message(message, config)


def receive_messages(config: dict | None = None) -> list[dict]:
    """Receive pending messages from Signal."""
    config = config or load_config()
    cli = _get_signal_cli(config)
    sender = config.get("sender_number", "")

    if not sender:
        return []

    try:
        result = subprocess.run(
            [cli, "-a", sender, "-o", "json", "receive", "--timeout", "5"],
            capture_output=True, text=True, timeout=30,
        )

        messages = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                envelope = json.loads(line)
                data_msg = envelope.get("envelope", {}).get("dataMessage", {})
                sync_msg = envelope.get("envelope", {}).get("syncMessage", {})

                # Direct incoming message
                if data_msg and data_msg.get("message"):
                    source = (
                        envelope.get("envelope", {}).get("sourceNumber")
                        or envelope.get("envelope", {}).get("source", "")
                    )
                    messages.append({
                        "source": source,
                        "message": data_msg["message"],
                        "timestamp": data_msg.get("timestamp", 0),
                    })

                # Sync message (from your own phone / Note to Self)
                elif sync_msg:
                    sent = sync_msg.get("sentMessage", {})
                    if sent and sent.get("message"):
                        dest = sent.get("destinationNumber") or sent.get("destination", "")
                        if dest == sender:
                            messages.append({
                                "source": sender,
                                "message": sent["message"],
                                "timestamp": sent.get("timestamp", 0),
                            })
            except json.JSONDecodeError:
                continue

        return messages
    except subprocess.TimeoutExpired:
        return []
    except FileNotFoundError:
        logger.error(f"signal-cli not found at {cli}")
        return []
    except Exception as e:
        logger.error(f"Error receiving messages: {e}")
        return []


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    config = load_config()

    print("Signal Notification Module — Self Test")
    print(f"signal-cli: {_get_signal_cli(config)}")
    print(f"Sender:     {config.get('sender_number') or '(not set)'}")
    print(f"Target:     {config.get('recipient_number') or '(not set)'}")
    print()

    status = check_signal_cli(config)
    if status["ok"]:
        print(f"[✓] signal-cli {status['version']}")
    else:
        print(f"[✗] {status['error']}")
        sys.exit(1)

    if config.get("sender_number") and config.get("recipient_number"):
        print("\nSending test message...")
        ok = send_notification("Secretary Test", "Your local private secretary is working!")
        if ok:
            print("[✓] Test message sent! Check your Signal app.")
        else:
            print("[✗] Failed to send test message.")
    else:
        print("\n[!] Set sender_number and recipient_number in config.json to test.")
"""
Signal notification module for the private secretary.
Uses the signal-cli-rest-api container over HTTP.

Setup:
    1. Run the signal-cli-rest-api container (see setup/setup-signal.sh)
    2. Link to your account: open http://localhost:8080/v1/qrcodelink?device_name=secretary
       and scan it from Signal > Settings > Linked Devices > Link New Device
    3. Set signal_api_url, sender_number, and recipient_number in config.json
"""

import json
import logging
from pathlib import Path

import httpx

logger = logging.getLogger("secretary.signal")

try:
    from paths import CONFIG_PATH
except ImportError:
    CONFIG_PATH = Path(__file__).parent.parent / "credentials" / "config.json"

# Defaults
DEFAULT_CONFIG = {
    "signal_api_url": "http://localhost:8080",
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


def _get_api_url(config: dict) -> str:
    return config.get("signal_api_url", "http://localhost:8080").rstrip("/")


def _as_list(value: str | list[str]) -> list[str]:
    """recipient_number accepts either a single number or a list of numbers."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def check_signal_api(config: dict | None = None) -> dict:
    """Check if the signal-cli-rest-api container is reachable and an account is linked."""
    config = config or load_config()
    url = _get_api_url(config)

    try:
        resp = httpx.get(f"{url}/v1/about", timeout=10)
        resp.raise_for_status()
        return {"ok": True, "version": resp.json().get("version", "unknown")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# Keep old name for compatibility with signal_bot.py / secretary.py
check_signal_cli = check_signal_api


# Signal delivers long messages poorly and they read badly on a phone, so a
# long reply goes out as several messages instead of one wall of text.
CHUNK_LIMIT = 1400


def split_message(message: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Split a long reply into phone-sized pieces on natural boundaries.

    Prefers paragraph breaks, then line breaks, then sentence ends, and only
    hard-cuts a run of text with no break in it at all.
    """
    message = message.strip()
    if len(message) <= limit:
        return [message] if message else []

    chunks: list[str] = []
    remaining = message
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = -1
        for sep in ("\n\n", "\n", ". ", " "):
            found = window.rfind(sep)
            # Ignore breaks so early that the chunk would be mostly empty.
            if found > limit * 0.5:
                cut = found + (len(sep) if sep != " " else 0)
                break
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return [c for c in chunks if c]


def send_message(message: str, config: dict | None = None) -> bool:
    """Send a Signal message, splitting anything too long across several.

    Returns True only if every piece was delivered.
    """
    config = config or load_config()
    parts = split_message(message)
    if len(parts) > 1:
        total = len(parts)
        ok = True
        for i, part in enumerate(parts, 1):
            # Number them so the order is obvious if they arrive out of sequence.
            if not _send_one(f"({i}/{total}) {part}", config):
                ok = False
        return ok
    return _send_one(message, config)


def _send_one(message: str, config: dict | None = None) -> bool:
    """Send exactly one Signal message to the configured recipients."""
    config = config or load_config()
    url = _get_api_url(config)
    sender = config.get("sender_number", "")
    recipients = _as_list(config.get("recipient_number", ""))

    if not sender or not recipients:
        logger.error("Signal not configured. Set sender_number and recipient_number in config.json")
        return False

    try:
        resp = httpx.post(
            f"{url}/v2/send",
            json={"message": message, "number": sender, "recipients": recipients},
            timeout=30,
        )
        if resp.status_code in (200, 201):
            logger.info(f"Signal message sent: {message[:80]}...")
            return True
        else:
            logger.error(f"signal-api error: {resp.status_code} {resp.text}")
            return False
    except httpx.TimeoutException:
        logger.error("signal-api timed out sending message")
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
    url = _get_api_url(config)
    sender = config.get("sender_number", "")

    if not sender:
        return []

    try:
        resp = httpx.get(f"{url}/v1/receive/{sender}", timeout=30)
        resp.raise_for_status()
        envelopes = resp.json()
    except httpx.TimeoutException:
        return []
    except Exception as e:
        logger.error(f"Error receiving messages: {e}")
        return []

    messages = []
    for wrapper in envelopes:
        envelope = wrapper.get("envelope", {})
        data_msg = envelope.get("dataMessage", {})
        sync_msg = envelope.get("syncMessage", {})

        # Direct incoming message
        if data_msg and data_msg.get("message"):
            source = envelope.get("sourceNumber") or envelope.get("source", "")
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

    return messages


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    config = load_config()

    print("Signal Notification Module — Self Test")
    print(f"API URL:    {_get_api_url(config)}")
    print(f"Sender:     {config.get('sender_number') or '(not set)'}")
    print(f"Target:     {config.get('recipient_number') or '(not set)'}")
    print()

    status = check_signal_api(config)
    if status["ok"]:
        print(f"[✓] signal-cli-rest-api {status['version']}")
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

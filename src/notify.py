"""
One way for Savvy to reach the user: deliver(title, body, kind).

Every notification the scheduler and the liveness alerts send goes through here
(docs/app-api.md, "Notifications"). deliver():

  1. records it in memory/notifications.db, the feed the Android app polls
     (GET /v1/notifications) when push isn't set up;
  2. pushes a data-only Firebase Cloud Messaging message to every registered
     phone, when credentials/firebase_service_account.json exists;
  3. sends it over Signal too while the "signal_fallback" config key is on
     (the default, so nothing changes until the user turns it off).

This module is imported by liveness.py, which must keep working when the Google
or Anthropic libraries are broken. So only the standard library, paths and
notifier (httpx) are imported at the top; google-auth is imported inside the
FCM path and its absence just means "no push".
"""

import json
import logging
import os
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path

import notifier
import paths

logger = logging.getLogger("secretary.notify")

KINDS = ("bod", "eod", "checkin", "weekly", "reminder", "meal", "rest", "fact_check", "alert", "signin")
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
KEEP_ROWS = 500            # the feed is a mailbox for the phone, not an archive
FCM_BODY_BYTES = 3000      # FCM data payloads cap at 4 KB; leave room for the rest
MAX_TOKENS = 10


# ---------------------------------------------------------------------------
# Locations (functions, so tests can point paths at a temp dir)
# ---------------------------------------------------------------------------
def _db_path() -> Path:
    return paths.MEMORY_DIR / "notifications.db"


def _tokens_path() -> Path:
    return paths.CREDENTIALS_DIR / "app_fcm_tokens.json"


def _service_account_path() -> Path:
    return paths.CREDENTIALS_DIR / "firebase_service_account.json"


def _devices_path() -> Path:
    return paths.CREDENTIALS_DIR / "app_devices.json"


# ---------------------------------------------------------------------------
# The feed
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    # The scheduler, the systemd alert hook and the app server all write here.
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL)""")
    return conn


def record(title: str, body: str, kind: str) -> dict:
    """Store a notification and return it as the API shapes it."""
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute("INSERT INTO notifications (ts, kind, title, body) VALUES (?, ?, ?, ?)",
                           (ts, kind, title, body))
        row_id = cur.lastrowid
        conn.execute("DELETE FROM notifications WHERE id <= ?", (row_id - KEEP_ROWS,))
    conn.close()
    return {"id": row_id, "ts": ts, "kind": kind, "title": title, "body": body}


def feed(after_id: int = 0, limit: int = 50) -> list[dict]:
    """Notifications newer than after_id, oldest first."""
    limit = max(1, min(int(limit), 200))
    conn = _connect()
    try:
        rows = conn.execute("SELECT id, ts, kind, title, body FROM notifications WHERE id > ? "
                            "ORDER BY id LIMIT ?", (int(after_id), limit)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# FCM token registry
# ---------------------------------------------------------------------------
def _write_json_private(path: Path, data) -> None:
    """Atomic write, owner-only, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _paired_device_ids() -> set[str] | None:
    """Ids in credentials/app_devices.json (app_api.auth's file); None if unreadable."""
    try:
        devices = json.loads(_devices_path().read_text()).get("devices", [])
    except (OSError, ValueError, AttributeError):
        return None
    return {str(d.get("id")) for d in devices if isinstance(d, dict)}


def _token_entries() -> list[dict]:
    try:
        data = json.loads(_tokens_path().read_text())
    except (OSError, ValueError):
        return []
    return [t for t in data.get("tokens", []) if isinstance(t, dict) and t.get("token")]


def fcm_tokens() -> list[str]:
    """Tokens of phones that are still paired (unpairing a phone stops its pushes)."""
    paired = _paired_device_ids() or set()
    return [t["token"] for t in _token_entries()
            if t.get("device_id") is None or str(t["device_id"]) in paired]


def register_fcm_token(token: str, device_id: str | None = None) -> None:
    """Remember a phone's FCM token (one per device, newest last, at most MAX_TOKENS)."""
    entries = [t for t in _token_entries()
               if t["token"] != token and (device_id is None or t.get("device_id") != device_id)]
    entries.append({"token": token, "device_id": device_id, "added": int(time.time())})
    _write_json_private(_tokens_path(), {"tokens": entries[-MAX_TOKENS:]})


def remove_fcm_tokens(dead: set[str]) -> None:
    if dead:
        _write_json_private(_tokens_path(),
                            {"tokens": [t for t in _token_entries() if t["token"] not in dead]})


def has_app_device() -> bool:
    """A phone is paired, so it polls the feed (and may also get pushes)."""
    return bool(_paired_device_ids())


# ---------------------------------------------------------------------------
# FCM HTTP v1
# ---------------------------------------------------------------------------
def fcm_configured() -> bool:
    return _service_account_path().exists()


def _fcm_auth() -> tuple[str, str]:
    """(project_id, access token). Raises if google-auth is missing or the key is bad."""
    from google.oauth2 import service_account          # lazy: liveness must not need Google
    from google.auth.transport.requests import Request
    path = _service_account_path()
    project_id = json.loads(path.read_text())["project_id"]
    creds = service_account.Credentials.from_service_account_file(str(path), scopes=[FCM_SCOPE])
    creds.refresh(Request())
    return project_id, creds.token


def _http_client():
    import httpx
    return httpx.Client(timeout=15)


def _clip_utf8(text: str, limit: int) -> tuple[str, bool]:
    raw = text.encode()
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode(errors="ignore").rstrip() + "…", True


def fcm_message(token: str, note: dict) -> dict:
    """The data-only message for one device (values must all be strings)."""
    body, clipped = _clip_utf8(note["body"], FCM_BODY_BYTES)
    data = {"id": str(note["id"]), "kind": note["kind"], "title": note["title"][:200], "body": body}
    if clipped:
        data["truncated"] = "1"   # the app fetches the full text from the feed
    return {"message": {"token": token, "data": data, "android": {"priority": "high"}}}


def _is_dead_token(resp) -> bool:
    try:
        err = resp.json().get("error", {})
    except Exception:
        return False
    codes = {err.get("status")} | {d.get("errorCode") for d in err.get("details", []) if isinstance(d, dict)}
    return bool(codes & {"UNREGISTERED", "INVALID_ARGUMENT"})


def push(note: dict) -> int:
    """Send `note` to every registered phone; returns how many accepted it."""
    tokens = fcm_tokens()
    if not tokens or not fcm_configured():
        return 0
    try:
        project_id, access = _fcm_auth()
    except Exception as e:
        logger.error(f"FCM unavailable: {e}")
        return 0

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    sent, dead = 0, set()
    with _http_client() as client:
        for token in tokens:
            try:
                resp = client.post(url, json=fcm_message(token, note),
                                   headers={"Authorization": f"Bearer {access}"})
            except Exception as e:
                logger.error(f"FCM send failed: {e}")
                continue
            if resp.status_code == 200:
                sent += 1
            elif _is_dead_token(resp):
                dead.add(token)
            else:
                logger.error(f"FCM error {resp.status_code}: {resp.text[:300]}")
    if dead:
        logger.info(f"Dropping {len(dead)} FCM token(s) the phone no longer uses")
        remove_fcm_tokens(dead)
    return sent


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
def signal_enabled(config: dict) -> bool:
    return bool(config.get("signal_fallback", True))


def deliver(title: str, body: str, kind: str, config: dict | None = None,
            signal_text: str | None = None) -> bool:
    """Reach the user on every configured channel.

    `signal_text` replaces the Signal formatting ("📋 title\\n\\nbody") when the
    caller already wrote the whole message, as the liveness alerts do.

    Returns True when the user will see it somewhere: Signal or FCM accepted it,
    or it was recorded while a phone is paired (that phone polls the feed, so
    the notification reaches it within minutes). Recording with no phone at all
    is not delivery, so callers that retry or start cooldowns on success (the
    scheduler's daily cap, liveness's cooldown, requeued reminders) still see a
    failure when nothing can reach the user.
    """
    config = config if config is not None else notifier.load_config()
    if kind not in KINDS:
        logger.warning(f"Unknown notification kind {kind!r}; sending as 'alert'")
        kind = "alert"

    note = None
    try:
        note = record(title, body, kind)
    except Exception as e:
        logger.error(f"Couldn't record notification: {e}")

    pushed = 0
    if note is not None:
        try:
            pushed = push(note)
        except Exception as e:
            logger.error(f"FCM push failed: {e}")

    signalled = False
    if signal_enabled(config):
        if signal_text is not None:
            signalled = notifier.send_message(signal_text, config)
        else:
            signalled = notifier.send_notification(title, body, config)

    polled = note is not None and has_app_device()
    return bool(signalled or pushed or polled)

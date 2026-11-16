"""Device pairing and the bearer-token check.

Pairing: `app_server.py --pair` (or the tray) makes a 6-digit code that lives
10 minutes in credentials/app_pairing.json. The phone trades it once, through
POST /v1/pair, for a random token. Only hashes are kept on disk, so reading
the files doesn't give anyone a working code or token.

The code is the only thing standing between the network and the API, so wrong
guesses are counted: after MAX_BAD_TRIES within BAD_TRY_WINDOW the code is
thrown away and pairing is refused until the window passes or a new code is
made. A million codes at five guesses each can't be brute-forced.

The file is read on every pair attempt rather than cached, because the tray
makes codes in its own process while the server (another process) checks them.
"""

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import Request

import paths

CODE_TTL = 600                  # seconds a pairing code stays good
MAX_BAD_TRIES = 5
BAD_TRY_WINDOW = 600

_lock = threading.Lock()        # one pair attempt at a time, so a code is used once


def _pairing_path() -> Path:
    return paths.CREDENTIALS_DIR / "app_pairing.json"


def _devices_path() -> Path:
    return paths.CREDENTIALS_DIR / "app_devices.json"


def write_private_json(path: Path, data) -> None:
    """Replace a JSON file in one step, readable only by this user.

    Other processes read these files at any moment; half a file would look like
    a lost pairing or a broken config.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _hash(value: str, salt: str = "") -> str:
    return hashlib.sha256((salt + value).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Pairing codes
# ---------------------------------------------------------------------------
def new_pairing_code() -> str:
    """Make a fresh code, replacing any earlier one and clearing the wrong-guess count."""
    code = f"{secrets.randbelow(10**6):06d}"
    salt = secrets.token_hex(16)
    with _lock:
        write_private_json(_pairing_path(), {
            "code_sha256": _hash(code, salt), "salt": salt,
            "expires": time.time() + CODE_TTL, "bad_tries": [],
        })
    return code


class PairingRefused(Exception):
    """`reason` is "bad_code" or "too_many_attempts"."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def redeem_pairing_code(code: str, device_name: str) -> str:
    """Trade a pairing code for a new device token, or raise PairingRefused."""
    with _lock:
        path = _pairing_path()
        state = _read_json(path, {})
        now = time.time()
        bad = [t for t in state.get("bad_tries", []) if now - t < BAD_TRY_WINDOW]
        if len(bad) >= MAX_BAD_TRIES:
            raise PairingRefused("too_many_attempts")

        live = bool(state.get("code_sha256")) and now < state.get("expires", 0)
        given = _hash(str(code).strip(), state.get("salt", ""))
        if not (live and hmac.compare_digest(given, state["code_sha256"])):
            bad.append(now)
            state["bad_tries"] = bad
            if len(bad) >= MAX_BAD_TRIES:
                state.pop("code_sha256", None)       # this code is burnt
            write_private_json(path, state)
            raise PairingRefused("bad_code")

        path.unlink(missing_ok=True)                 # single use
        token = secrets.token_urlsafe(32)
        devices = _read_json(_devices_path(), {}).get("devices", [])
        devices.append({
            "id": secrets.token_hex(8),
            "name": (device_name or "phone").strip()[:64],
            "token_sha256": _hash(token),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })
        write_private_json(_devices_path(), {"devices": devices})
        return token


def revoke_all_devices() -> int:
    """Forget every paired device. Returns how many there were."""
    with _lock:
        devices = _read_json(_devices_path(), {}).get("devices", [])
        write_private_json(_devices_path(), {"devices": []})
        return len(devices)


# ---------------------------------------------------------------------------
# The check every protected route runs
# ---------------------------------------------------------------------------
def device_for_token(token: str) -> dict | None:
    if not token:
        return None
    wanted = _hash(token)
    found = None
    for device in _read_json(_devices_path(), {}).get("devices", []):
        # compare_digest on every row, so timing says nothing about near misses
        if hmac.compare_digest(wanted, str(device.get("token_sha256", ""))):
            found = device
    return found


async def require_device(request: Request) -> dict:
    """The paired device making this request; 401 unpaired otherwise."""
    from app_api.server import ApiError
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    device = device_for_token(token.strip()) if scheme.lower() == "bearer" else None
    if not device:
        raise ApiError(401, "unpaired", "This phone isn't paired with Savvy. Pair it again.")
    return device

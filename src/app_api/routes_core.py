"""Ping, pair, status, settings, memory facts, confirm levels.

Unlike the other route modules, `router` has no blanket auth dependency: ping
and pair are how an unpaired phone finds and joins the server. Every other
route here adds Depends(require_device) itself.

Routes that touch disk or the network are plain `def`, so FastAPI runs them in
its thread pool instead of stalling the event loop.
"""

import json
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

import paths
from app_api.auth import PairingRefused, redeem_pairing_code, require_device, write_private_json
from app_api.server import ApiError

router = APIRouter(prefix="/v1")
authed = [Depends(require_device)]

OLLAMA_URL = "http://localhost:11434/api/tags"


def load_config() -> dict:
    try:
        return json.loads(paths.CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Public: ping and pair
# ---------------------------------------------------------------------------
@router.get("/ping")
def ping():
    return {"ok": True, "name": "Savvy", "version": "1"}


class PairBody(BaseModel):
    code: str
    device_name: str = ""


@router.post("/pair")
def pair(body: PairBody):
    try:
        token = redeem_pairing_code(body.code, body.device_name)
    except PairingRefused as e:
        if e.reason == "too_many_attempts":
            raise ApiError(429, "too_many_attempts",
                           "Too many wrong codes. Make a new code on the PC and try again.")
        raise ApiError(403, "bad_code", "That code is wrong or has expired. Make a new one on the PC.")
    print(f"[app_server] Paired a new device: {body.device_name or 'phone'}")
    return {"token": token}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def _google_status() -> list[dict]:
    import google_auth as ga
    rows = []
    for label in ga.get_account_labels():
        try:
            creds = ga.get_credentials(label, interactive=False)
        except Exception as e:
            print(f"[app_server] Checking Google '{label}' failed: {e}")
            creds = None
        connected = bool(creds and creds.valid)
        reason = None
        if not connected:
            reason = ga.signin_needed().get(label) or "couldn't reach Google"
        rows.append({"label": label, "email": ga.stored_account_email(label) or None,
                     "connected": connected, "reason": reason})
    return rows


def _ollama_up() -> bool:
    try:
        return httpx.get(OLLAMA_URL, timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


@router.get("/status", dependencies=authed)
def status():
    config = load_config()
    return {
        "google": _google_status(),
        "todoist": bool((config.get("todoist_api_token") or "").strip()),
        "ollama": _ollama_up(),
        "signal_fallback": bool(config.get("signal_fallback", True)),
    }


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
# Defaults are the ones scheduler.py falls back on, so an unset key shows what
# actually happens. rest_day_warnings lives at rest_days.enabled (rest.py's
# settings block).
SETTING_DEFAULTS = {
    "bod_time": "08:30", "eod_time": "22:00",
    "checkin_times": ["09:30", "13:00", "16:30"],
    "weekly_review_day": "Sunday", "weekly_review_time": "18:00",
    "quiet_hours_start": 23, "quiet_hours_end": 7,
    "rest_day_warnings": True,
    "signal_fallback": True,
}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def _read_settings(config: dict) -> dict:
    out = {k: config.get(k, v) for k, v in SETTING_DEFAULTS.items()
           if k != "rest_day_warnings"}
    out["rest_day_warnings"] = bool((config.get("rest_days") or {}).get("enabled", True))
    return out


def _check_setting(key: str, value):
    """The value to store for `key`, or raise ApiError 400 saying what's wrong."""
    def bad(why: str):
        raise ApiError(400, "bad_value", f"{key}: {why}")

    if key in ("bod_time", "eod_time", "weekly_review_time"):
        if not (isinstance(value, str) and _HHMM.fullmatch(value)):
            bad("use a 24-hour time like 08:30")
        return value
    if key == "checkin_times":
        if not (isinstance(value, list) and len(value) <= 12
                and all(isinstance(t, str) and _HHMM.fullmatch(t) for t in value)):
            bad("use a list of 24-hour times like [\"09:30\", \"13:00\"]")
        return sorted(set(value))
    if key == "weekly_review_day":
        day = value.strip().capitalize() if isinstance(value, str) else ""
        if day not in WEEKDAYS:
            bad("use a weekday name like Sunday")
        return day      # the scheduler compares against strftime("%A")
    if key in ("quiet_hours_start", "quiet_hours_end"):
        if type(value) is not int or not 0 <= value <= 23:
            bad("use a whole hour from 0 to 23")
        return value
    if type(value) is not bool:
        bad("use true or false")
    return value


@router.get("/settings", dependencies=authed)
def get_settings():
    return _read_settings(load_config())


@router.put("/settings", dependencies=authed)
def put_settings(body: dict):
    unknown = sorted(set(body) - set(SETTING_DEFAULTS))
    if unknown:
        raise ApiError(400, "unknown_setting", f"Unknown setting(s): {', '.join(unknown)}")
    clean = {k: _check_setting(k, v) for k, v in body.items()}

    config = load_config()          # re-read, so keys other tools wrote survive
    for key, value in clean.items():
        if key == "rest_day_warnings":
            config["rest_days"] = {**(config.get("rest_days") or {}), "enabled": value}
        else:
            config[key] = value
    write_private_json(paths.CONFIG_PATH, config)
    return _read_settings(config)


# ---------------------------------------------------------------------------
# Memory facts and confirm levels
# ---------------------------------------------------------------------------
def _db():
    import memory
    return memory._get_db()


@router.get("/memory/facts", dependencies=authed)
def list_facts(q: str = ""):
    db = _db()
    try:
        rows = db.execute(
            "SELECT id, fact, fact_type, timestamp FROM facts "
            "WHERE fact LIKE ? COLLATE NOCASE ORDER BY timestamp DESC",
            (f"%{q.strip()}%",),
        ).fetchall()
    finally:
        db.close()
    return [{"id": r[0], "fact": r[1], "type": r[2],
             "updated": datetime.fromtimestamp(r[3]).astimezone().isoformat(timespec="seconds")}
            for r in rows]


@router.delete("/memory/facts/{fact_id}", dependencies=authed)
def delete_fact(fact_id: int):
    db = _db()
    try:
        gone = db.execute("DELETE FROM facts WHERE id = ?", (fact_id,)).rowcount
        db.commit()
    finally:
        db.close()
    if not gone:
        raise ApiError(404, "not_found", "No fact with that id.")
    return {"ok": True}


@router.get("/confirm-levels", dependencies=authed)
def confirm_levels():
    """Danger levels from tool_config.json; tools not listed there are level 2."""
    import tool_diagnostics
    try:
        data = json.loads(tool_diagnostics.CONFIG_PATH.read_text())
        return {k: int(v) for k, v in sorted(data.get("danger_levels", {}).items())}
    except (OSError, ValueError, AttributeError):
        return {}

"""
Place book — the handful of locations you actually go, kept locally.

Travel-time estimation needs two endpoints, and calendar events almost never
carry a usable address (1% of them, when this was written). So places are
resolved by nickname instead: "lab", "school", "the gym" all map to a stored
entry with a real address and coordinates.

Lives in notes/places.json, which is gitignored — it holds home and work
addresses and should never reach the repo.

Entries look like:
    {
      "key": "lab",
      "name": "Northeastern lab",
      "address": "360 Huntington Ave, Boston, MA 02115",
      "lat": 42.3398, "lon": -71.0892,
      "aliases": ["school", "northeastern", "campus"],
      "default_mode": "walk"
    }

`overrides` stores durations you've confirmed yourself, in seconds, keyed
"<from>|<to>|<mode>". Those always beat a routing estimate — you know your
commute better than a router does.
"""

import json
from pathlib import Path

from paths import NOTES_DIR

PLACES_PATH = NOTES_DIR / "places.json"

# Plain names, mapped to Google's travelMode enum in travel.py.
VALID_MODES = {"transit", "walk", "bike", "drive"}
DEFAULT_MODE = "transit"

# Places saved under the earlier OpenRouteService profile names.
_LEGACY_MODES = {
    "foot-walking": "walk",
    "cycling-regular": "bike",
    "driving-car": "drive",
    "wheelchair": "walk",
}


def normalize_mode(mode: str | None) -> str:
    """Accept a legacy ORS profile name or a current one; fall back to default."""
    if not mode:
        return DEFAULT_MODE
    mode = _norm(mode)
    mode = _LEGACY_MODES.get(mode, mode)
    return mode if mode in VALID_MODES else DEFAULT_MODE


def _empty() -> dict:
    return {"places": [], "overrides": {}}


def load() -> dict:
    try:
        data = json.loads(PLACES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return _empty()
    data.setdefault("places", [])
    data.setdefault("overrides", {})
    return data


def save(data: dict) -> None:
    PLACES_PATH.write_text(json.dumps(data, indent=2) + "\n")


def list_places() -> list[dict]:
    return load()["places"]


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def find(query: str) -> dict | None:
    """Resolve a nickname to a place. Exact key/name/alias first, then substring.

    Substring matching only counts when exactly one place matches, so "the gym"
    resolving to two different gyms returns nothing rather than the wrong one.
    """
    q = _norm(query)
    if not q:
        return None
    places = load()["places"]

    for p in places:
        if _norm(p.get("key")) == q or _norm(p.get("name")) == q:
            return p
        if any(_norm(a) == q for a in p.get("aliases", [])):
            return p

    hits = []
    for p in places:
        haystack = [p.get("key", ""), p.get("name", ""), *p.get("aliases", [])]
        if any(q in _norm(h) or _norm(h) in q for h in haystack if h):
            hits.append(p)
    return hits[0] if len(hits) == 1 else None


def add(key: str, name: str, address: str = "", lat: float | None = None,
        lon: float | None = None, aliases: list[str] | None = None,
        default_mode: str = DEFAULT_MODE) -> dict:
    """Add or update a place. Re-adding an existing key merges into it."""
    key = _norm(key).replace(" ", "-")
    if not key:
        raise ValueError("A place needs a key.")
    default_mode = normalize_mode(default_mode)

    data = load()
    entry = None
    for p in data["places"]:
        if p.get("key") == key:
            entry = p
            break
    if entry is None:
        entry = {"key": key}
        data["places"].append(entry)

    entry["name"] = name or entry.get("name") or key
    if address:
        entry["address"] = address
    if lat is not None and lon is not None:
        entry["lat"], entry["lon"] = float(lat), float(lon)
    if aliases:
        merged = {_norm(a) for a in entry.get("aliases", [])} | {_norm(a) for a in aliases}
        entry["aliases"] = sorted(a for a in merged if a)
    entry["default_mode"] = default_mode

    save(data)
    return entry


def remove(key: str) -> bool:
    data = load()
    before = len(data["places"])
    data["places"] = [p for p in data["places"] if p.get("key") != _norm(key)]
    if len(data["places"]) == before:
        return False
    save(data)
    return True


# ---------------------------------------------------------------------------
# Confirmed durations — these beat any routing estimate
# ---------------------------------------------------------------------------
def _override_key(a: str, b: str, mode: str) -> str:
    return f"{_norm(a)}|{_norm(b)}|{normalize_mode(mode)}"


def get_override(a: str, b: str, mode: str) -> int | None:
    data = load()["overrides"]
    for key in (_override_key(a, b, mode), _override_key(b, a, mode)):
        if key in data:
            try:
                return int(data[key])
            except (TypeError, ValueError):
                return None
    return None


def set_override(a: str, b: str, mode: str, seconds: int) -> None:
    data = load()
    data["overrides"][_override_key(a, b, mode)] = int(seconds)
    save(data)


def format_places_for_context(places: list[dict] | None = None) -> str:
    places = places if places is not None else list_places()
    if not places:
        return "(no places saved yet)"
    lines = []
    for p in places:
        line = f"- {p.get('name')} [{p.get('key')}]"
        if p.get("aliases"):
            line += " aka " + ", ".join(p["aliases"])
        if p.get("address"):
            line += f" — {p['address']}"
        if not p.get("address"):
            line += "  (no address — only confirmed times work for it)"
        lines.append(line)
    return "\n".join(lines)

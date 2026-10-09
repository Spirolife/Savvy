"""
Travel-time estimation via the Google Routes API, with a local cache.

Google rather than OpenRouteService for one reason: ORS has no public-transit
routing, and the T is the primary way this user gets around. A driving estimate
is a bad proxy for a subway trip.

Resolution order, cheapest and most trustworthy first:

    1. A duration you confirmed yourself (places.overrides) — you know your own
       commute better than any router, and it needs no address or API call.
    2. The on-disk cache, keyed by route + mode + rough time of day.
    3. Google Routes API.

Everything degrades to "unknown" rather than raising, so a missing key or a
network blip never blocks scheduling.

Notes on the API:
  * Endpoint is POST /directions/v2:computeRoutes. Origin and destination take
    plain address strings, so no separate geocoding step is needed.
  * The X-Goog-FieldMask header decides both response size AND billing tier.
    Asking only for routes.duration keeps this on the cheapest SKU — do not
    widen it to transit step details without a reason.
  * Transit results depend on departure time (the T is thinner late at night),
    so a real departure time is passed when one is known. That is schedule
    data, not live traffic. Google rejects a departureTime in the past, so it
    is clamped forward.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

import places
from paths import CONFIG_PATH, MEMORY_DIR

CACHE_PATH = MEMORY_DIR / "travel_cache.json"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
TIMEOUT = 20.0
CACHE_TTL_SECONDS = 60 * 60 * 24 * 30

# Transit answers come from published timetables, which only run so far ahead.
# Measured against the live API: a date ~60 days out still routes, ~112 does not.
# Past this, ask about the equivalent nearby day instead of getting nothing.
SCHEDULE_HORIZON_DAYS = 45

# Our plain mode names -> Google's travelMode enum.
_GOOGLE_MODE = {
    "transit": "TRANSIT",
    "walk": "WALK",
    "bike": "BICYCLE",
    "drive": "DRIVE",
}


class TravelError(RuntimeError):
    """Raised only by explicit setup helpers, never by estimate()."""


def _config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _api_key() -> str:
    return (os.environ.get("GOOGLE_MAPS_API_KEY")
            or _config().get("google_maps_api_key") or "").strip()


def default_mode() -> str:
    return places.normalize_mode(_config().get("default_travel_mode"))


def buffer_seconds() -> int:
    try:
        return int(_config().get("travel_buffer_minutes", 5)) * 60
    except (TypeError, ValueError):
        return 300


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(cache, indent=2) + "\n")
    except OSError:
        pass


def _bucket(when: datetime | None) -> str:
    """Coarse daypart + day type. Transit frequency varies by both — a weekday
    rush-hour number is wrong on a Sunday, so they cache separately."""
    if when is None:
        return "any"
    hour = when.hour
    if hour < 6:
        part = "night"
    elif hour < 10:
        part = "am-peak"
    elif hour < 16:
        part = "midday"
    elif hour < 20:
        part = "pm-peak"
    else:
        part = "evening"
    day = "wknd" if when.weekday() >= 5 else "wkdy"
    return f"{day}-{part}"


def _representative_time(when: datetime | None) -> datetime | None:
    """Pull a far-future time back inside the timetable horizon.

    Beyond it the API returns no transit route at all. Rather than giving up on
    anything scheduled months out, ask about the same weekday and clock time on
    the soonest equivalent day — which is what "how long does this normally
    take" means anyway.
    """
    if when is None:
        return None
    now = datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    if when <= now + timedelta(days=SCHEDULE_HORIZON_DAYS):
        return when
    candidate = now.replace(hour=when.hour, minute=when.minute, second=0, microsecond=0)
    while candidate <= now or candidate.weekday() != when.weekday():
        candidate += timedelta(days=1)
    return candidate


def _cache_get(key: str) -> int | None:
    entry = _load_cache().get(key)
    if not entry or time.time() - entry.get("at", 0) > CACHE_TTL_SECONDS:
        return None
    return entry.get("seconds")


def _cache_put(key: str, seconds: int) -> None:
    cache = _load_cache()
    cache[key] = {"seconds": int(seconds), "at": time.time()}
    _save_cache(cache)


# ---------------------------------------------------------------------------
# Google Routes
# ---------------------------------------------------------------------------
def _waypoint(place: dict) -> dict | None:
    """Prefer coordinates when a place has them, else its address string."""
    if place.get("lat") is not None and place.get("lon") is not None:
        return {"location": {"latLng": {"latitude": float(place["lat"]),
                                        "longitude": float(place["lon"])}}}
    if place.get("address"):
        return {"address": place["address"]}
    return None


def _departure(when: datetime | None) -> str:
    """RFC3339 UTC. Google rejects past departure times, so clamp forward."""
    now = datetime.now(timezone.utc)
    if when is None:
        when = now + timedelta(minutes=5)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    if when <= now:
        when = now + timedelta(minutes=5)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _routes_duration(a: dict, b: dict, mode: str, when: datetime | None) -> int | None:
    key = _api_key()
    origin, destination = _waypoint(a), _waypoint(b)
    if not key or not origin or not destination:
        return None

    body = {
        "origin": origin,
        "destination": destination,
        "travelMode": _GOOGLE_MODE[mode],
    }
    # departureTime is only sent for TRANSIT. Driving defaults to
    # TRAFFIC_UNAWARE routing, and Google rejects the pair outright
    # ("Timestamp cannot be set for TRAFFIC_UNAWARE routing mode"), which
    # silently killed every drive estimate. Making it traffic-aware instead
    # would move to a pricier SKU for a mode this user never takes.
    if mode == "transit":
        # Timetables run out; a far-future date returns no route at all.
        body["departureTime"] = _departure(_representative_time(when))
    try:
        r = httpx.post(
            ROUTES_URL, timeout=TIMEOUT, json=body,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": key,
                # Narrow mask = smallest response and cheapest billing tier.
                "X-Goog-FieldMask": "routes.duration",
            },
        )
        r.raise_for_status()
        routes = r.json().get("routes") or []
        if not routes:
            return None
        raw = routes[0].get("duration")  # e.g. "1234s"
        return int(float(str(raw).rstrip("s")))
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError):
        return None


def check_api_key() -> dict:
    """Explicit connectivity probe for the setup script. Raises on failure."""
    key = _api_key()
    if not key:
        raise TravelError(
            "No Google Maps API key. Create one in Google Cloud Console with the "
            "Routes API enabled, then set 'google_maps_api_key' in "
            "credentials/config.json (or the GOOGLE_MAPS_API_KEY env var)."
        )
    body = {
        "origin": {"address": "Northeastern University, Boston, MA"},
        "destination": {"address": "Boston Common, Boston, MA"},
        "travelMode": "TRANSIT",
        "departureTime": _departure(None),
    }
    try:
        r = httpx.post(ROUTES_URL, timeout=TIMEOUT, json=body, headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": "routes.duration",
        })
    except httpx.RequestError as e:
        raise TravelError(f"Could not reach the Routes API: {e}") from e
    if r.status_code == 403:
        # A malformed key returns 400 INVALID_ARGUMENT instead, so a 403 here
        # means Google recognizes the key but will not authorize Routes for it.
        raise TravelError(
            "Routes API rejected the key (403 PERMISSION_DENIED). The key itself is "
            "valid — an invalid one returns 400 — so this is a project/authorization "
            "problem. Check, in order:\n"
            "  1. The KEY's own API restrictions include Routes API (Credentials -> "
            "the key -> API restrictions). This is the usual culprit: enabling the API "
            "on the project and letting a given key call it are separate settings, and "
            "a key restricted to other APIs fails here even when Routes is enabled.\n"
            "  2. 'Routes API' is ENABLED on the key's project. Note it is a distinct "
            "product from the older 'Directions API' / 'Distance Matrix API' — "
            "enabling those does not enable Routes.\n"
            "  3. A billing account is attached to the project. Routes requires one "
            "even inside the free allowance.\n"
            "  4. Application restrictions are 'None' or allow a server IP — an HTTP "
            "referrer restriction blocks server-side calls like this one.")
    if r.status_code >= 400:
        raise TravelError(f"Routes API {r.status_code}: {r.text[:300]}")
    routes = r.json().get("routes") or []
    if not routes:
        raise TravelError("Key works but the probe route returned nothing.")
    seconds = int(float(str(routes[0]["duration"]).rstrip("s")))
    return {"ok": True, "probe_minutes": round(seconds / 60)}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def estimate(origin: str, destination: str, mode: str | None = None,
             when: datetime | None = None) -> dict:
    """Estimate travel time between two named places. Never raises.

    Returns {ok, seconds, minutes, source, origin, destination, mode, note};
    `source` is confirmed / cached / google / unknown.
    """
    mode = places.normalize_mode(mode)

    a = places.find(origin)
    b = places.find(destination)
    result = {"ok": False, "seconds": None, "minutes": None, "source": "unknown",
              "origin": origin, "destination": destination, "mode": mode, "note": ""}

    if not a or not b:
        missing = [n for n, p in ((origin, a), (destination, b)) if not p]
        result["note"] = (f"Not in the place book: {', '.join(missing)}. "
                          "Save it with save_place, then this works.")
        return result

    result["origin"], result["destination"] = a["name"], b["name"]

    if a["key"] == b["key"]:
        result.update(ok=True, seconds=0, minutes=0, source="confirmed",
                      note="Same place — no travel needed.")
        return result

    confirmed = places.get_override(a["key"], b["key"], mode)
    if confirmed is not None:
        result.update(ok=True, seconds=confirmed, minutes=round(confirmed / 60),
                      source="confirmed")
        return result

    # Directional: transit is not symmetric — different lines, schedules and
    # walk-to-stop legs each way. Confirmed overrides stay bidirectional, since
    # a person saying "those are 5 minutes apart" means both ways.
    cache_key = f"{a['key']}>{b['key']}|{mode}|{_bucket(when)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        result.update(ok=True, seconds=cached, minutes=round(cached / 60), source="cached")
        return result

    if not _waypoint(a) or not _waypoint(b):
        noaddr = [p["name"] for p in (a, b) if not _waypoint(p)]
        result["note"] = (f"No address saved for: {', '.join(noaddr)}. Add one with "
                          "save_place, or record the time with save_travel_time.")
        return result

    seconds = _routes_duration(a, b, mode, when)
    if seconds is None:
        result["note"] = ("No estimate available — check the Google Maps API key, or "
                          "record the real time with save_travel_time.")
        return result

    _cache_put(cache_key, seconds)
    result.update(ok=True, seconds=seconds, minutes=round(seconds / 60), source="google")
    return result


def check_gap(origin: str, destination: str, gap_seconds: int,
              mode: str | None = None, when: datetime | None = None) -> dict:
    """Does `gap_seconds` cover the trip, plus the configured buffer?"""
    est = estimate(origin, destination, mode, when)
    buffer_s = buffer_seconds()
    out = dict(est)
    out["gap_minutes"] = round(gap_seconds / 60)
    out["buffer_minutes"] = round(buffer_s / 60)

    if not est["ok"]:
        out["feasible"] = None
        return out

    needed = est["seconds"] + buffer_s
    out["needed_minutes"] = round(needed / 60)
    out["feasible"] = gap_seconds >= needed
    out["slack_minutes"] = round((gap_seconds - needed) / 60)
    return out

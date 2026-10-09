#!/usr/bin/env python3
"""
setup-travel.py — connect the Google Routes API for travel-time estimates.

Run:  python setup/setup-travel.py

Why Google and not a free alternative: OpenRouteService and most open routers
have no public-transit data. If you get around by subway or bus, they cannot
answer the question at all.

Getting a key:
  1. https://console.cloud.google.com/  — reuse the project you made for
     Calendar/Gmail, or create a new one.
  2. Enable the "Routes API" for that project.
  3. APIs & Services -> Credentials -> Create credentials -> API key.
  4. Restrict the key to the Routes API. It is a plain key, not OAuth, so
     anyone holding it can spend against your project.
  5. Attach a billing account. Routes API requires one even inside the free
     allowance.

Cost: Savvy asks only for `routes.duration`, which is the cheapest field mask,
and caches every answer for a month per route and time-of-day. Ordinary
scheduling should stay inside the free monthly allowance — but the allowance is
per-SKU and Google changes it, so check current pricing rather than trusting a
number written here.

The key is stored in credentials/config.json (gitignored, chmod 600).
"""

import getpass
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

try:
    import httpx  # noqa: F401
except ImportError:
    sys.exit("httpx is not installed here.\n"
             "Activate the venv first:  source .venv/bin/activate")

import places   # noqa: E402
import travel   # noqa: E402
from paths import CONFIG_PATH  # noqa: E402


def save_key(token: str) -> None:
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        cfg = {}
    cfg["google_maps_api_key"] = token
    cfg.pop("openrouteservice_api_key", None)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    CONFIG_PATH.chmod(0o600)
    print(f"[✓] Key saved to {CONFIG_PATH} (mode 600)")


def main() -> int:
    key = travel._api_key()
    if key:
        print(f"Using existing Google Maps key (…{key[-4:]}).")
    else:
        print(__doc__.split("Getting a key:")[1].split("Cost:")[0].rstrip())
        key = getpass.getpass("\nPaste your Google Maps API key (hidden): ").strip()
        if not key:
            print("[✗] No key entered.")
            return 1
        save_key(key)

    try:
        probe = travel.check_api_key()
    except travel.TravelError as e:
        print(f"[✗] {e}")
        return 1
    print(f"[✓] Routes API works — test transit route took {probe['probe_minutes']} min")

    saved = places.list_places()
    print(f"\nSaved places ({len(saved)}):")
    print(places.format_places_for_context())

    no_address = [p["name"] for p in saved if not p.get("address")]
    if no_address:
        print(f"\n[!] No address for: {', '.join(no_address)}")
        print("    Routed estimates need one. Confirmed times still work without it.")
    if not places.find("home"):
        print("\n[!] No 'home' saved. Most travel checks start there — tell Savvy:")
        print('    "save my home address as <street, city>"')

    print("\n[✓] Travel estimation ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

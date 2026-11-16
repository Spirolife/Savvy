"""The HTTP server the Savvy Android app talks to (docs/app-api.md).

    python src/app_server.py              run it (the tray does this for you)
    python src/app_server.py --pair       print a 6-digit code to pair a phone
    python src/app_server.py --revoke-all unpair every phone

It listens on app_api_host:app_api_port from credentials/config.json, by default
127.0.0.1:8765 so nothing outside this computer can reach it until you choose
an address (docs/android-setup.md). Logs go to stdout; the tray keeps them.
"""

import argparse
import json
import os
import sys

from paths import CONFIG_PATH

DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 8765


def _config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}


def main() -> None:
    ap = argparse.ArgumentParser(description="Savvy app server")
    ap.add_argument("--pair", action="store_true", help="print a pairing code for a phone")
    ap.add_argument("--revoke-all", action="store_true", help="unpair every phone")
    ap.add_argument("--host", help="address to listen on (overrides app_api_host)")
    ap.add_argument("--port", type=int, help="port to listen on (overrides app_api_port)")
    args = ap.parse_args()

    if args.pair:
        from app_api.auth import CODE_TTL, new_pairing_code
        print(f"Pairing code: {new_pairing_code()}  (good for {CODE_TTL // 60} minutes, once)")
        return
    if args.revoke_all:
        from app_api.auth import revoke_all_devices
        print(f"Unpaired {revoke_all_devices()} device(s).")
        return

    os.environ["SAVVY_UNATTENDED"] = "1"   # never wait on a browser sign-in
    try:
        from google_auth import start_signin_listener
        start_signin_listener()
    except Exception as e:
        print(f"[app_server] Google sign-in links won't finish on this computer: {e}")

    import uvicorn
    from app_api.server import create_app

    config = _config()
    host = args.host or config.get("app_api_host") or DEFAULT_HOST
    port = args.port or int(config.get("app_api_port") or DEFAULT_PORT)
    print(f"[app_server] Listening on http://{host}:{port}", flush=True)
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    sys.exit(main())

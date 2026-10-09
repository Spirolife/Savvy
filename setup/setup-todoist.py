#!/usr/bin/env python3
"""
setup-todoist.py — store a Todoist API token and verify it end to end.

Run from anywhere:  python setup/setup-todoist.py

Gets the token from (in order) the TODOIST_API_TOKEN env var, the existing
credentials/config.json, or an interactive prompt, then makes real read-only
calls so you find out here — not mid-conversation — whether Savvy can talk
to Todoist.

The token is written to credentials/config.json, which is gitignored. The
file is chmod 600 on write since it also holds the Anthropic API key.
"""

import getpass
import json
import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

try:
    import httpx
except ImportError:
    sys.exit("httpx is not installed in this interpreter.\n"
             "Activate the venv first, e.g.  source secretaryvenv/bin/activate")

from paths import CONFIG_PATH  # noqa: E402

BASE_URL = "https://api.todoist.com/api/v1"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_token(config: dict, token: str) -> None:
    config["todoist_api_token"] = token
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
    CONFIG_PATH.chmod(0o600)
    print(f"[✓] Token saved to {CONFIG_PATH} (mode 600)")


def check(token: str) -> bool:
    headers = {"Authorization": f"Bearer {token}"}

    def get(path, **params):
        r = httpx.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=20.0)
        r.raise_for_status()
        data = r.json()
        return data.get("results", data) if isinstance(data, dict) else data

    try:
        projects = get("/projects", limit=200)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            print("[✗] Todoist rejected the token (401). Get a fresh one at:")
            print("    Todoist → Settings → Integrations → Developer → API token")
        else:
            print(f"[✗] Todoist returned {e.response.status_code}: {e.response.text[:200]}")
        return False
    except httpx.RequestError as e:
        print(f"[✗] Could not reach Todoist: {e}")
        return False

    print(f"\n[✓] Authenticated. {len(projects)} project(s):")
    for p in projects:
        inbox = " (inbox)" if p.get("inbox_project") or p.get("is_inbox_project") else ""
        print(f"    - {p.get('name')}{inbox}")

    try:
        due = get("/tasks/filter", query="today | overdue", limit=50)
        print(f"\n[✓] Filter query works. {len(due)} task(s) due today or overdue:")
        for t in due[:10]:
            print(f"    - {t.get('content')}")
        if not due:
            print("    (nothing due — that's fine, the query still ran)")
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        print(f"[!] Projects worked but the task filter failed: {e}")
        print("    Reads may be partly broken; check the API status page.")
        return False

    return True


def main() -> int:
    config = load_config()
    token = (os.environ.get("TODOIST_API_TOKEN") or config.get("todoist_api_token") or "").strip()
    source = "TODOIST_API_TOKEN" if os.environ.get("TODOIST_API_TOKEN") else "config.json"

    if token:
        print(f"Using existing token from {source} (…{token[-4:]}).")
    else:
        print("Get your token: Todoist → Settings → Integrations → Developer → API token")
        token = getpass.getpass("Paste your Todoist API token (input hidden): ").strip()
        if not token:
            print("[✗] No token entered.")
            return 1

    if not check(token):
        return 1

    if token != config.get("todoist_api_token"):
        save_token(config, token)

    print("\n[✓] Todoist is ready. Savvy will pick up the task tools on next start.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

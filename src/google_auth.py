"""
Google OAuth2 authentication — multi-account support.
Each account gets its own token file (token_<label>.json).

NOTE: If you change SCOPES, delete the existing token_*.json files
      and re-authorize each account.
"""

import json
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

try:
    from paths import CREDENTIALS_DIR as BASE_DIR, GOOGLE_CREDENTIALS_FILE as CREDENTIALS_FILE
except ImportError:
    BASE_DIR = Path(__file__).parent.parent / "credentials"
    CREDENTIALS_FILE = BASE_DIR / "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/calendar",            # Full calendar access
    "https://www.googleapis.com/auth/gmail.modify",         # Read, send, modify, label, archive
    "https://www.googleapis.com/auth/gmail.send",           # Send email
    "https://www.googleapis.com/auth/tasks",                # Full tasks access
]


def _token_path(label: str) -> Path:
    return BASE_DIR / f"token_{label}.json"


def get_account_labels() -> list[str]:
    labels = []
    for f in BASE_DIR.glob("token_*.json"):
        label = f.stem.replace("token_", "")
        labels.append(label)
    return sorted(labels)


def get_credentials(label: str) -> Credentials | None:
    token_file = _token_path(label)
    creds = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(token_file, "w") as f:
                f.write(creds.to_json())
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if not CREDENTIALS_FILE.exists():
            return None
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_file, "w") as f:
            f.write(creds.to_json())

    return creds


def get_all_credentials() -> dict[str, Credentials]:
    result = {}
    for label in get_account_labels():
        creds = get_credentials(label)
        if creds and creds.valid:
            result[label] = creds
    return result


def check_google_setup() -> dict:
    labels = get_account_labels()
    return {
        "credentials_file": CREDENTIALS_FILE.exists(),
        "accounts": labels,
        "count": len(labels),
    }


if __name__ == "__main__":
    print("Google OAuth2 Setup — Multi-Account")
    print("=" * 45)

    if not CREDENTIALS_FILE.exists():
        print(f"\n[!] credentials.json not found at:")
        print(f"    {CREDENTIALS_FILE}")
        print()
        print("Setup instructions:")
        print("  1. Go to https://console.cloud.google.com/")
        print("  2. Create a project, enable Calendar API + Gmail API")
        print("  3. OAuth consent screen > add emails as test users")
        print("  4. Credentials > Create > OAuth client ID > Desktop app")
        print("  5. Download JSON, save as credentials.json")
        print("  6. Run: python google_auth.py <label>")
        sys.exit(1)

    existing = get_account_labels()
    if existing:
        print(f"\nExisting accounts: {', '.join(existing)}")

    if len(sys.argv) < 2:
        print("\nUsage: python google_auth.py <label>")
        print("  e.g. python google_auth.py personal")
        print("       python google_auth.py northeastern")
        if existing:
            print(f"\nAlready connected: {', '.join(existing)}")
        sys.exit(0)

    label = sys.argv[1].strip().lower()
    token_file = _token_path(label)

    if token_file.exists():
        print(f"\n[•] Token for '{label}' already exists.")
        confirm = input("    Re-authorize? (yes/no): ").strip().lower()
        if confirm != "yes":
            sys.exit(0)
        token_file.unlink()

    print(f"\n[•] Authorizing account '{label}'...")
    print("    A browser will open — log in with the correct Google account.")

    creds = get_credentials(label)
    if creds and creds.valid:
        print(f"\n[✓] Account '{label}' authorized!")
        print(f"    Scopes: calendar (full), gmail (read + send)")
        all_labels = get_account_labels()
        print(f"    Connected accounts: {', '.join(all_labels)}")
    else:
        print(f"\n[✗] Authorization failed for '{label}'.")
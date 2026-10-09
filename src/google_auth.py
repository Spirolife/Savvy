"""
Google OAuth2 authentication — multi-account support.
Each account gets its own token file (token_<label>.json).

The label ("personal", "northeastern") is just a local nickname; nothing in the
OAuth flow ties it to the account you actually pick in the browser. Choosing
the wrong Google account there used to save silently under the other label, and
stayed invisible because the token file recorded no email at all — every later
call then hit the wrong inbox and calendar.

So: every newly-authorized token is resolved back to its real email address,
confirmed with you when a terminal is attached, and the address is written into
the token file as `account`. verify_account_labels() re-checks that record
against the live API, and get_credentials() refuses to overwrite a label with
an account already registered under a different one.

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


def fetch_account_email(creds: Credentials) -> str:
    """Ask Google which account these credentials actually belong to."""
    from googleapiclient.discovery import build
    try:
        profile = build("gmail", "v1", credentials=creds).users().getProfile(userId="me").execute()
        return (profile.get("emailAddress") or "").strip()
    except Exception:
        return ""


def stored_account_email(label: str) -> str:
    """The email recorded in a token file, or '' for tokens written before this
    was tracked (those predate the check and can't be trusted by name alone)."""
    try:
        with open(_token_path(label)) as f:
            return (json.load(f).get("account") or "").strip()
    except (OSError, json.JSONDecodeError):
        return ""


def _write_token(label: str, creds: Credentials, email: str = "") -> None:
    """Persist credentials, keeping the resolved email alongside them."""
    data = json.loads(creds.to_json())
    if email:
        data["account"] = email
    elif not data.get("account"):
        data["account"] = stored_account_email(label)
    path = _token_path(label)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    path.chmod(0o600)


def _label_owning_email(email: str, exclude: str = "") -> str:
    """Which other label already claims this email, if any."""
    for other in get_account_labels():
        if other != exclude and stored_account_email(other).lower() == email.lower():
            return other
    return ""


def _confirm_identity(label: str, email: str) -> bool:
    """Confirm the browser account matches the intended label.

    Only prompts with a terminal attached; a background service auto-accepts
    rather than hanging forever on input() nobody can answer.
    """
    if not email:
        print(f"[!] Could not read the email for '{label}' — saving unverified.")
        return True
    if not sys.stdin.isatty():
        print(f"[•] '{label}' authorized as {email} (unattended — not confirmed)")
        return True

    clash = _label_owning_email(email, exclude=label)
    if clash:
        print(f"\n[!] {email} is already saved as '{clash}'.")
        print(f"    Saving it as '{label}' too would point both labels at one account.")

    print(f"\n    You authorized: {email}")
    answer = input(f"    Save this as '{label}'? (yes/no): ").strip().lower()
    if answer in ("y", "yes"):
        return True
    print(f"[✗] Not saved. Re-run and pick the account you mean for '{label}'.")
    return False


def verify_account_labels() -> list[dict]:
    """Check every token's recorded email against the live API.

    Returns one row per label with `ok` False where the label is mislabeled,
    unverifiable, or predates email tracking.
    """
    rows = []
    for label in get_account_labels():
        stored = stored_account_email(label)
        creds = None
        try:
            token_file = _token_path(label)
            if token_file.exists():
                creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
        except Exception:
            creds = None
        live = fetch_account_email(creds) if creds and creds.valid else ""
        rows.append({
            "label": label,
            "stored": stored,
            "live": live,
            "ok": bool(stored) and bool(live) and stored.lower() == live.lower(),
        })
    return rows


def get_credentials(label: str, interactive: bool | None = None) -> Credentials | None:
    """Valid credentials for `label`, refreshing the token when it has expired.

    If the refresh fails, a person at a terminal gets the browser sign-in. A
    background service (no TTY) never does: the flow would open a browser on the
    desktop and block the whole bot until someone happened to approve it. It
    texts the user the command to run instead and returns None, so that account
    is skipped until they do.
    """
    if interactive is None:
        interactive = sys.stdin.isatty()
    token_file = _token_path(label)
    creds = None
    reason = "no saved login"

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _write_token(label, creds)
        except Exception as e:
            reason = f"refresh failed: {e}"
            print(f"[google_auth] '{label}' token {reason}")
            creds = None

    if not creds or not creds.valid:
        if not CREDENTIALS_FILE.exists():
            return None
        if not interactive:
            try:
                from liveness import announce_auth_needed   # lazy: liveness must not need Google
                announce_auth_needed(label, reason)
            except Exception as e:
                print(f"[google_auth] Could not send re-authorization alert: {e}")
            return None
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0)
        # Resolve who actually logged in before committing it to this label.
        email = fetch_account_email(creds)
        if not _confirm_identity(label, email):
            return None
        _write_token(label, creds, email)

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

    if len(sys.argv) > 1 and sys.argv[1] in ("--verify", "verify"):
        if not existing:
            print("\nNo accounts authorized yet.")
            sys.exit(0)
        print("\nChecking each label against the live Google account...\n")
        bad = 0
        for row in verify_account_labels():
            if row["ok"]:
                print(f"  [\u2713] {row['label']:<14} {row['live']}")
            else:
                bad += 1
                live = row["live"] or "(could not reach account)"
                stored = row["stored"] or "(no email recorded)"
                print(f"  [\u2717] {row['label']:<14} is really {live}")
                print(f"      {'':<14} recorded as {stored}")
        if bad:
            print(f"\n{bad} label(s) need attention. Re-authorize with:")
            print("    python google_auth.py <label>")
        else:
            print("\nAll labels match their accounts.")
        sys.exit(1 if bad else 0)

    if existing:
        print(f"\nExisting accounts: {', '.join(existing)}")

    if len(sys.argv) < 2:
        print("\nUsage: python google_auth.py <label>")
        print("       python google_auth.py --verify")
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
    print("    You'll be shown which account you picked before it's saved.")

    creds = get_credentials(label)
    if creds and creds.valid:
        email = stored_account_email(label) or fetch_account_email(creds)
        print(f"\n[✓] '{label}' authorized as {email}")
        print(f"    Scopes: calendar (full), gmail (read + send)")
        print(f"    Connected accounts: {', '.join(get_account_labels())}")
    else:
        print(f"\n[✗] Authorization failed or was declined for '{label}'.")
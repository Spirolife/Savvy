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

Signing in again without a terminal: signin_link() makes a Google link that a
background service can text or show. Google sends the browser back to a
localhost address when it's done, so:

  - clicked on the computer, the listener here catches it and finishes by itself;
  - tapped on a phone, that last page can't load (localhost is the phone). The
    address in its URL bar still holds the one-time code, so the user sends it
    to Savvy and finish_signin_from_text() completes the sign-in.

Links live in credentials/signin_pending/, one file each, because the Signal
bot, the scheduler and the tray are separate processes: any of them can make a
link and any of them can finish it.

NOTE: If you change SCOPES, delete the existing token_*.json files
      and re-authorize each account.
"""

import html
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow, InstalledAppFlow

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

# Fixed, so a link sent earlier still works after a service restarts. Google's
# desktop-app clients accept any localhost port without registering it.
SIGNIN_PORT = 38917
SIGNIN_DIR = BASE_DIR / "signin_pending"
SIGNIN_MAX_AGE = 7 * 86400          # how long a link already sent keeps working
SIGNIN_REUSE_AGE = 6 * 86400        # after this, hand out a new link rather than a nearly-dead one
CONSOLE_URL = "https://console.cloud.google.com/apis/credentials"
PHONE_SIGNIN_TIP = ("On a phone, the last page won't load after you approve. That's expected: "
                    "copy its address and send it to me here. On the computer it finishes by itself.")

# label -> why that account can't be used right now. Per process: each one
# finds out by trying, and a success on any later call clears it.
_needs_signin: dict[str, str] = {}


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
    # Replaced in one step: other processes read this file on every call, and
    # half a file would look like a broken login.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


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
    background service never does, even one started from a terminal: the flow
    would open a browser on the desktop and block the whole bot until someone
    happened to approve it. It texts the user a sign-in link instead and returns
    None, so that account is skipped until they use it.

    Failing to reach Google isn't a sign-in problem, so that returns None too but
    asks nothing of the user.
    """
    if interactive is None:
        interactive = sys.stdin.isatty() and not os.environ.get("SAVVY_UNATTENDED")
    token_file = _token_path(label)
    creds = None
    reason = "no saved login"

    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except ValueError:
            reason = "the saved login is unreadable"

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _write_token(label, creds)
        except RefreshError as e:
            print(f"[google_auth] '{label}' token refresh failed: {e}")
            reason = "the login expired or was revoked"
            creds = None
        except Exception as e:
            print(f"[google_auth] '{label}' couldn't reach Google to refresh: {e}")
            return None

    if not creds or not creds.valid:
        if not interactive:
            _needs_signin[label] = reason
        if not CREDENTIALS_FILE.exists():
            return None
        if not interactive:
            try:
                from liveness import announce_auth_needed   # lazy: liveness must not need Google
                link = _try_signin_link(label)
                announce_auth_needed(label, reason,
                                     f"Sign in:\n{link}\n\n{PHONE_SIGNIN_TIP}" if link else "")
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

    _needs_signin.pop(label, None)
    return creds


def get_all_credentials() -> dict[str, Credentials]:
    result = {}
    for label in get_account_labels():
        creds = get_credentials(label)
        if creds and creds.valid:
            result[label] = creds
    return result


# ---------------------------------------------------------------------------
# Signing in by link
# ---------------------------------------------------------------------------
def signin_needed() -> dict[str, str]:
    """Accounts this process found unusable, as {label: reason}."""
    return dict(_needs_signin)


def _pending_path(state: str) -> Path | None:
    """Where a link's details live. `state` comes from a URL, so it can't choose the path."""
    return SIGNIN_DIR / f"{state}.json" if re.fullmatch(r"[A-Za-z0-9_-]{16,128}", state) else None


def _pending_links() -> list[tuple[Path, dict]]:
    """Every live link, deleting the ones too old to honor."""
    live = []
    for path in SIGNIN_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            if time.time() - data["created"] < SIGNIN_MAX_AGE:
                live.append((path, data))
                continue
        except (OSError, ValueError, KeyError, TypeError):
            pass
        path.unlink(missing_ok=True)
    return live


def signin_link(label: str) -> str:
    """A Google sign-in link for this account, or '' with no OAuth client to make one."""
    if not CREDENTIALS_FILE.exists():
        return ""
    start_signin_listener()
    for _, data in _pending_links():
        if data.get("label") == label and time.time() - data["created"] < SIGNIN_REUSE_AGE:
            return data["url"]

    flow = Flow.from_client_secrets_file(
        str(CREDENTIALS_FILE), SCOPES, redirect_uri=f"http://localhost:{SIGNIN_PORT}/")
    hint = stored_account_email(label)
    url, state = flow.authorization_url(
        access_type="offline",   # without these two Google can omit the refresh token,
        prompt="consent",        # and the login would die again within the hour
        **({"login_hint": hint} if hint else {}),  # opens on the right account
    )
    SIGNIN_DIR.mkdir(mode=0o700, exist_ok=True)
    with open(os.open(_pending_path(state), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump({"label": label, "url": url, "verifier": flow.code_verifier,
                   "redirect_uri": flow.redirect_uri, "created": time.time()}, f)
    return url


def _try_signin_link(label: str) -> str:
    """signin_link, but a broken client file still lets the warning go out."""
    try:
        return signin_link(label)
    except Exception as e:
        print(f"[google_auth] Could not make a sign-in link for '{label}': {e}")
        return ""


def complete_signin(state: str, code: str) -> tuple[bool, str]:
    """Trade a redirect's one-time code for a saved login. Returns (ok, what happened)."""
    path = _pending_path(state)
    try:
        pending = json.loads(path.read_text()) if path else None
    except (OSError, ValueError):
        pending = None
    if not pending:
        return False, "That sign-in link has expired or was already used."

    label = pending["label"]
    try:
        flow = Flow.from_client_secrets_file(
            str(CREDENTIALS_FILE), SCOPES, state=state,
            redirect_uri=pending["redirect_uri"], code_verifier=pending["verifier"])
        flow.fetch_token(code=code)
    except Warning:          # oauthlib raises this when fewer scopes came back
        return False, (f"Google only granted some of the permissions for '{label}'. "
                       "Sign in again and leave every box ticked.")
    except Exception as e:
        print(f"[google_auth] Sign-in for '{label}' failed: {e}")
        return False, f"Google wouldn't complete the sign-in for '{label}'. Try the link once more."

    # Same guard as the terminal flow: never file one account under another's label.
    creds = flow.credentials
    email = fetch_account_email(creds)
    expected = stored_account_email(label)
    clash = _label_owning_email(email, exclude=label) if email else ""
    if email and expected and email.lower() != expected.lower():
        return False, (f"That was {email}, but '{label}' is {expected}. Nothing was saved. "
                       f"Use the link again and pick {expected}.")
    if clash:
        return False, f"{email} is already saved as '{clash}', so it wasn't saved as '{label}'."

    _write_token(label, creds, email)
    _needs_signin.pop(label, None)
    for other, data in _pending_links():
        if data.get("label") == label:
            other.unlink(missing_ok=True)
    return True, f"'{label}' is signed in again" + (f" as {email}." if email else ".")


_PASTED_REDIRECT = re.compile(r"(?:https?://)?(?:localhost|127\.0\.0\.1)(?::\d+)?/?\?\S+")


def finish_signin_from_text(text: str, markdown: bool = False) -> str | None:
    """Finish a sign-in from a pasted redirect address. None if `text` isn't one.

    Callers must check messages here before logging or storing them, because
    the address holds the one-time code.
    """
    match = _PASTED_REDIRECT.search(text)
    if not match:
        return None
    query = parse_qs(urlsplit("http://" + match.group(0).split("://")[-1]).query)
    state, code = query.get("state", [""])[0], query.get("code", [""])[0]
    if not state or not (code or "error" in query):
        return None
    if not code:
        ok, message = False, "Google cancelled that sign-in, so nothing changed."
    else:
        ok, message = complete_signin(state, code)
    if ok:
        return f"✅ {message}"
    notice = signin_notice(markdown)
    return f"⚠️ {message}" + (f"\n\n{notice}" if notice else "")


def signin_notice(markdown: bool = False) -> str:
    """What to show the user for every account waiting on a sign-in; '' if none."""
    needed = signin_needed()
    if not needed:
        return ""
    lines, have_link = [], False
    for label in needed:
        url = _try_signin_link(label)
        have_link = have_link or bool(url)
        if url and markdown:
            lines.append(f"[Sign in to Google: {label}]({url})")
        elif url:
            lines.append(f"Sign in to Google ({label}):\n{url}")
        else:
            lines.append(f"Google '{label}' needs signing in, but no link could be made. "
                         f"Check credentials/credentials.json ({CONSOLE_URL}).")
    if have_link and not markdown:
        lines.append(PHONE_SIGNIN_TIP)
    return "\n\n".join(lines)


_SIGNIN_PAGE = ("<!doctype html><meta charset=utf-8><title>Savvy</title>"
                "<body style='font:18px system-ui;margin:15vh auto;max-width:30em;padding:0 1em'>"
                "<h2>{title}</h2><p>{body}</p>")


class _SigninHandler(BaseHTTPRequestHandler):
    timeout = 10                     # a stalled connection mustn't hold up the next one

    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        state, code = query.get("state", [""])[0], query.get("code", [""])[0]
        if not state:
            self.send_error(404)
            return
        if code:
            ok, message = complete_signin(state, code)
        else:
            ok, message = False, "The sign-in was cancelled, so nothing changed."
        page = _SIGNIN_PAGE.format(title="You're all set" if ok else "Not signed in",
                                   body=html.escape(message)
                                   + (" You can close this tab." if ok else ""))
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode())

    def log_message(self, *args):    # the default logs the URL, which holds the code
        pass


class _SigninServer(HTTPServer):
    allow_reuse_address = False      # on Windows it would let two processes share the port


_listener_lock = threading.Lock()
_listener_started = False


def start_signin_listener() -> None:
    """Catch sign-in redirects on this computer, in whichever process gets the port.

    The others keep retrying, so if the one holding it stops, another takes over
    and links that were already sent keep working. Localhost only.
    """
    global _listener_started
    with _listener_lock:
        if _listener_started:
            return
        _listener_started = True

    def serve():
        while True:
            try:
                server = _SigninServer(("127.0.0.1", SIGNIN_PORT), _SigninHandler)
            except OSError:
                time.sleep(30)
                continue
            server.serve_forever()

    threading.Thread(target=serve, name="google-signin", daemon=True).start()


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
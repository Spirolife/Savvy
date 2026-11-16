"""Google config, connect (server auth code exchange), disconnect.

The phone signs in natively and sends a one-time server auth code minted for
the PC's "Web application" OAuth client. Only the PC holds that client's
secret, so only the PC can turn the code into a refresh token. The result is
saved exactly like a browser sign-in, with the same identity checks.

Codes and tokens are never printed: the tray copies this process's output
into a log file.
"""

import json
import re

from fastapi import APIRouter, Depends
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel

import google_auth as ga
from app_api.auth import require_device
from app_api.server import ApiError

router = APIRouter(prefix="/v1", dependencies=[Depends(require_device)])

_LABEL = re.compile(r"[a-z0-9_-]{1,32}")


def web_client_file():
    return ga.BASE_DIR / "web_client.json"


def _check_label(label: str) -> str:
    if not _LABEL.fullmatch(label or ""):
        raise ApiError(400, "bad_label", "Labels are 1-32 lowercase letters, digits, - or _.")
    return label


def _web_client_id() -> str:
    try:
        return json.loads(web_client_file().read_text())["web"]["client_id"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ApiError(503, "no_web_client",
                       "The PC has no Google web client yet: save it as credentials/web_client.json "
                       "(see docs/android-setup.md).")


@router.get("/google/config")
def google_config():
    return {"web_client_id": _web_client_id(), "scopes": list(ga.SCOPES)}


class ConnectBody(BaseModel):
    label: str
    server_auth_code: str


def _exchange(code: str):
    """Trade an Android server auth code for credentials; ApiError 400 if Google says no."""
    try:
        # Android server auth codes carry no redirect and no PKCE verifier.
        flow = Flow.from_client_secrets_file(str(web_client_file()), scopes=ga.SCOPES,
                                             redirect_uri="", autogenerate_code_verifier=False)
    except (OSError, ValueError, KeyError) as e:
        print(f"[app_server] Web client file is unusable: {type(e).__name__}")
        raise ApiError(503, "no_web_client", "credentials/web_client.json is missing or unreadable.")
    try:
        flow.fetch_token(code=code)
    except Warning as w:
        # oauthlib raises this whenever the granted scopes differ from the asked.
        # Extra scopes are harmless; missing ones would break tools later.
        granted = set(getattr(w, "new_scope", None) or [])
        token = getattr(w, "token", None)
        if not token or not set(ga.SCOPES) <= granted:
            raise ApiError(400, "exchange_failed",
                           "Google only granted some of the permissions. Sign in again and allow them all.")
        flow.oauth2session.token = token
    except Exception as e:
        print(f"[app_server] Google code exchange failed: {type(e).__name__}")
        raise ApiError(400, "exchange_failed", "Google wouldn't accept that sign-in. Try again.")
    return flow.credentials


@router.post("/google/connect")
def google_connect(body: ConnectBody):
    label = _check_label(body.label)
    _web_client_id()                    # 503 before spending the code
    creds = _exchange(body.server_auth_code)
    email = ga.fetch_account_email(creds)
    problem, message = ga.identity_problem(label, email)
    if problem:
        raise ApiError(409, problem, message)
    again = ga._token_path(label).exists()
    ga.save_signin(label, creds, email)
    who = f" as {email}" if email else ""
    print(f"[app_server] Google '{label}' connected from the app{who}")
    return {"ok": True, "email": email or None,
            "message": f"'{label}' is {'signed in again' if again else 'connected'}{who}."}


@router.delete("/google/{label}")
def google_disconnect(label: str):
    _check_label(label)
    ga._token_path(label).unlink(missing_ok=True)
    ga._needs_signin.pop(label, None)
    return {"ok": True}

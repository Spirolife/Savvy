"""Inbox, threads, message actions. OWNER: chat-inbox agent.

Mail comes through email_integration (Gmail, every signed-in account). The
summary card is a short model summary of important unread mail, cached for
15 minutes and generated off the request thread so it never holds up the inbox.
"""

import re
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import email_integration as mail
from app_api.auth import require_device
from app_api.server import ApiError

router = APIRouter(prefix="/v1", dependencies=[Depends(require_device)])

LABEL_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
SUMMARY_TTL = 15 * 60       # seconds a summary is reused
SUMMARY_WAIT = 4.0          # longest the inbox waits for a fresh summary
SUMMARY_MODEL = "claude-haiku-4-5"

ACTIONS = {         # action -> email_integration function, looked up at call time
    "archive": "archive_email",
    "star": "star_email",
    "unstar": "unstar_email",
    "read": "mark_read",
    "unread": "mark_unread",
    "trash": "trash_email",
}


def _check_account(account: str) -> None:
    if not LABEL_RE.match(account or ""):
        raise ApiError(400, "bad_account", "Unknown account label.")


def _iso_ms(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def list_messages(account: str | None, query: str, limit: int) -> list[dict]:
    """Newest messages matching a Gmail query, as Mail dicts, across accounts.

    email_integration.search_emails doesn't keep labels (unread, starred) or
    filter by account, so this reads the same Gmail services directly.
    """
    found = []
    for label, service in mail._get_services():
        if account and label != account:
            continue
        try:
            result = service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
            for ref in result.get("messages", []):
                msg = service.users().messages().get(
                    userId="me", id=ref["id"], format="metadata",
                    metadataHeaders=["From", "Subject", "Date"]).execute()
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                labels = msg.get("labelIds", []) or []
                found.append({
                    "id": ref["id"],
                    "thread_id": msg.get("threadId", ""),
                    "account": label,
                    "from": headers.get("From", ""),
                    "subject": headers.get("Subject", "") or "(no subject)",
                    "snippet": msg.get("snippet", "")[:200],
                    "date": _iso_ms(msg.get("internalDate")),
                    "unread": "UNREAD" in labels,
                    "starred": "STARRED" in labels,
                    "important": "IMPORTANT" in labels,
                    "_ms": int(msg.get("internalDate") or 0),
                })
        except Exception as e:
            print(f"[app_api] Inbox fetch failed for {label}: {e}")
    found.sort(key=lambda m: m["_ms"], reverse=True)
    return [{k: v for k, v in m.items() if k != "_ms"} for m in found[:limit]]


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
_summary = {"text": None, "at": None}       # last result and when (monotonic)
_summary_lock = threading.Lock()
_summary_thread: threading.Thread | None = None


def generate_summary() -> str | None:
    """One or two sentences about important unread mail; None when there is nothing or on error."""
    try:
        important = mail.get_important_unread(max_results=5)
        if not important:
            return None
        import core
        from app_api.routes_chat import get_client
        client = get_client()
        config = core.load_app_config()
        lines = "\n".join(
            f"- [{m.get('account', '')}] {m.get('from', '')}: {m.get('subject', '')}: {m.get('snippet', '')}"
            for m in important)
        response = client.messages.create(
            model=config.get("inbox_summary_model", SUMMARY_MODEL),
            max_tokens=200,
            messages=[{"role": "user", "content": (
                "Summarize these important unread emails for me in one or two short sentences, "
                "plain text, no preamble. Say who needs what from me.\n\n" + lines)}],
        )
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text").strip()
        return text or None
    except Exception as e:
        print(f"[app_api] Inbox summary failed: {e}")
        return None


def _refresh_summary() -> None:
    text = generate_summary()
    with _summary_lock:
        _summary["text"], _summary["at"] = text, time.monotonic()


def get_summary() -> str | None:
    """The cached summary; refreshes it in the background when stale, waiting briefly."""
    global _summary_thread
    with _summary_lock:
        fresh = _summary["at"] is not None and time.monotonic() - _summary["at"] < SUMMARY_TTL
        if fresh:
            return _summary["text"]
        if _summary_thread is None or not _summary_thread.is_alive():
            _summary_thread = threading.Thread(target=_refresh_summary, daemon=True, name="inbox-summary")
            _summary_thread.start()
        worker = _summary_thread
    worker.join(SUMMARY_WAIT)
    with _summary_lock:
        return _summary["text"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/inbox")
def inbox(account: str = "all", q: str = "", limit: int = 30):
    account = (account or "all").strip()
    if account != "all":
        _check_account(account)
    limit = max(1, min(limit, 100))
    query = q.strip() or "in:inbox"
    try:
        unread = mail.get_unread_count()
    except Exception as e:
        print(f"[app_api] Unread counts failed: {e}")
        unread = {}
    messages = list_messages(None if account == "all" else account, query, limit)
    return {"summary": get_summary(), "unread": unread, "messages": messages}


@router.get("/inbox/{account}/threads/{thread_id}")
def thread(account: str, thread_id: str):
    _check_account(account)
    data = mail.read_thread(thread_id, account=account)
    if not data:
        raise ApiError(404, "not_found", "That conversation couldn't be found.")
    msgs = data.get("messages", [])
    subject = next((m.get("subject") for m in msgs if m.get("subject")), "") or "(no subject)"
    return {"subject": subject, "messages": [
        {"id": m.get("id", ""), "from": m.get("from", ""), "to": m.get("to", ""),
         "date": m.get("date", ""), "body_text": m.get("body", "")} for m in msgs]}


class ActionBody(BaseModel):
    action: str


@router.post("/inbox/{account}/messages/{message_id}/action")
def message_action(account: str, message_id: str, body: ActionBody):
    _check_account(account)
    name = ACTIONS.get(body.action)
    if name is None:
        raise ApiError(400, "bad_action", f"Unknown action '{body.action}'.")
    if not getattr(mail, name)(message_id, account=account):
        raise ApiError(502, "action_failed", "Gmail didn't accept that change.")
    return {"ok": True}

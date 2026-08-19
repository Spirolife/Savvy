"""
Gmail integration — multi-account. Full read, send, modify, label, thread support.
"""

import base64
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from googleapiclient.discovery import build

from google_auth import get_all_credentials


def _get_services() -> list[tuple[str, object]]:
    services = []
    for label, creds in get_all_credentials().items():
        try:
            svc = build("gmail", "v1", credentials=creds)
            services.append((label, svc))
        except Exception:
            pass
    return services


def _get_service_for_account(account=None):
    services = _get_services()
    if account:
        for label, svc in services:
            if label == account:
                return (label, svc)
    return services[0] if services else None


def _decode_body(payload):
    """Recursively extract plain text from email payload."""
    body_text = ""
    if payload.get("body", {}).get("data"):
        try:
            body_text = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        except Exception:
            pass
    if payload.get("parts"):
        for part in payload["parts"]:
            mime = part.get("mimeType", "")
            if mime == "text/plain" and part.get("body", {}).get("data"):
                try:
                    body_text += base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                except Exception:
                    pass
            elif mime.startswith("multipart/"):
                body_text += _decode_body(part)
    return body_text


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def get_recent_emails(max_results=10, hours_back=24):
    all_emails = []
    after = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    query = f"after:{int(after.timestamp())}"
    for label, service in _get_services():
        try:
            result = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
            for msg_ref in result.get("messages", []):
                msg = service.users().messages().get(userId="me", id=msg_ref["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"]).execute()
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                all_emails.append({"id": msg_ref["id"], "from": headers.get("From", ""), "subject": headers.get("Subject", "(no subject)"), "date": headers.get("Date", ""), "snippet": msg.get("snippet", "")[:150], "labels": msg.get("labelIds", []), "is_unread": "UNREAD" in msg.get("labelIds", []), "account": label})
        except Exception as e:
            print(f"[email] Error fetching from {label}: {e}")
    return all_emails


def get_unread_count():
    counts = {}
    for label, service in _get_services():
        try:
            result = service.users().messages().list(userId="me", q="is:unread in:inbox", maxResults=1).execute()
            counts[label] = result.get("resultSizeEstimate", 0)
        except Exception:
            counts[label] = 0
    return counts


def get_important_unread(max_results=5):
    all_emails = []
    for label, service in _get_services():
        try:
            result = service.users().messages().list(userId="me", q="is:unread (is:important OR is:starred)", maxResults=max_results).execute()
            for msg_ref in result.get("messages", []):
                msg = service.users().messages().get(userId="me", id=msg_ref["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"]).execute()
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                all_emails.append({"from": headers.get("From", ""), "subject": headers.get("Subject", "(no subject)"), "snippet": msg.get("snippet", "")[:150], "account": label, "is_unread": True})
        except Exception as e:
            print(f"[email] Error fetching from {label}: {e}")
    return all_emails


def search_emails(query, max_results=5):
    all_emails = []
    for label, service in _get_services():
        try:
            result = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
            for msg_ref in result.get("messages", []):
                msg = service.users().messages().get(userId="me", id=msg_ref["id"], format="metadata", metadataHeaders=["From", "To", "Subject", "Date"]).execute()
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                all_emails.append({"id": msg_ref["id"], "from": headers.get("From", ""), "to": headers.get("To", ""), "subject": headers.get("Subject", "(no subject)"), "date": headers.get("Date", ""), "snippet": msg.get("snippet", "")[:200], "account": label, "thread_id": msg.get("threadId", "")})
        except Exception:
            pass
    return all_emails


def read_full_email(message_id, account=None):
    """Read the full body of an email."""
    for label, service in _get_services():
        if account and label != account:
            continue
        try:
            msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            body = _decode_body(msg.get("payload", {}))
            return {
                "id": message_id,
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "cc": headers.get("Cc", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "body": body[:5000],
                "labels": msg.get("labelIds", []),
                "account": label,
                "thread_id": msg.get("threadId", ""),
            }
        except Exception as e:
            print(f"[email] Error reading full email: {e}")
    return None


def read_thread(thread_id, account=None):
    """Read an entire email thread (all messages in a conversation)."""
    for label, service in _get_services():
        if account and label != account:
            continue
        try:
            thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
            messages = []
            for msg in thread.get("messages", []):
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                body = _decode_body(msg.get("payload", {}))
                messages.append({
                    "id": msg["id"],
                    "from": headers.get("From", ""),
                    "to": headers.get("To", ""),
                    "date": headers.get("Date", ""),
                    "subject": headers.get("Subject", ""),
                    "body": body[:3000],
                })
            return {"thread_id": thread_id, "messages": messages, "account": label}
        except Exception as e:
            print(f"[email] Error reading thread: {e}")
    return None


# ---------------------------------------------------------------------------
# Modifying
# ---------------------------------------------------------------------------
def modify_email(message_id, add_labels=None, remove_labels=None, account=None):
    """Modify labels on an email. Used for star, archive, mark read/unread."""
    for label, service in _get_services():
        if account and label != account:
            continue
        try:
            body = {}
            if add_labels:
                body["addLabelIds"] = add_labels
            if remove_labels:
                body["removeLabelIds"] = remove_labels
            result = service.users().messages().modify(userId="me", id=message_id, body=body).execute()
            return True
        except Exception as e:
            print(f"[email] Error modifying email: {e}")
    return False


def star_email(message_id, account=None):
    return modify_email(message_id, add_labels=["STARRED"], account=account)


def unstar_email(message_id, account=None):
    return modify_email(message_id, remove_labels=["STARRED"], account=account)


def mark_read(message_id, account=None):
    return modify_email(message_id, remove_labels=["UNREAD"], account=account)


def mark_unread(message_id, account=None):
    return modify_email(message_id, add_labels=["UNREAD"], account=account)


def archive_email(message_id, account=None):
    return modify_email(message_id, remove_labels=["INBOX"], account=account)


def trash_email(message_id, account=None):
    for label, service in _get_services():
        if account and label != account:
            continue
        try:
            service.users().messages().trash(userId="me", id=message_id).execute()
            return True
        except Exception as e:
            print(f"[email] Error trashing email: {e}")
    return False


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
def list_labels(account=None):
    all_labels = []
    for label, service in _get_services():
        if account and label != account:
            continue
        try:
            result = service.users().labels().list(userId="me").execute()
            for lbl in result.get("labels", []):
                all_labels.append({"id": lbl["id"], "name": lbl.get("name", ""), "type": lbl.get("type", ""), "account": label})
        except Exception:
            pass
    return all_labels


def create_label(name, account=None):
    svc = _get_service_for_account(account)
    if not svc:
        return None
    label, service = svc
    try:
        result = service.users().labels().create(userId="me", body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}).execute()
        return {"id": result["id"], "name": result["name"], "account": label}
    except Exception as e:
        print(f"[email] Error creating label: {e}")
        return None


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------
def list_drafts(max_results=10, account=None):
    all_drafts = []
    for label, service in _get_services():
        if account and label != account:
            continue
        try:
            result = service.users().drafts().list(userId="me", maxResults=max_results).execute()
            for draft in result.get("drafts", []):
                msg = draft.get("message", {})
                # Get headers
                full_draft = service.users().drafts().get(userId="me", id=draft["id"], format="metadata", metadataHeaders=["To", "Subject"]).execute()
                headers = {h["name"]: h["value"] for h in full_draft.get("message", {}).get("payload", {}).get("headers", [])}
                all_drafts.append({"id": draft["id"], "message_id": msg.get("id", ""), "to": headers.get("To", ""), "subject": headers.get("Subject", "(no subject)"), "account": label})
        except Exception as e:
            print(f"[email] Error listing drafts: {e}")
    return all_drafts


def send_draft(draft_id, account=None):
    svc = _get_service_for_account(account)
    if not svc:
        return None
    label, service = svc
    try:
        result = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
        return {"id": result.get("id"), "thread_id": result.get("threadId"), "account": label}
    except Exception as e:
        print(f"[email] Error sending draft: {e}")
        return None


def delete_draft(draft_id, account=None):
    svc = _get_service_for_account(account)
    if not svc:
        return False
    label, service = svc
    try:
        service.users().drafts().delete(userId="me", id=draft_id).execute()
        return True
    except Exception as e:
        print(f"[email] Error deleting draft: {e}")
        return False


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def send_email(to, subject, body, account=None, cc="", bcc="", reply_to_id=None):
    svc = _get_service_for_account(account)
    if not svc:
        return None
    label, service = svc
    message = MIMEText(body)
    message["To"] = to
    message["Subject"] = subject
    if cc:
        message["Cc"] = cc
    if bcc:
        message["Bcc"] = bcc
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    send_body = {"raw": raw}
    if reply_to_id:
        try:
            orig = service.users().messages().get(userId="me", id=reply_to_id, format="minimal").execute()
            send_body["threadId"] = orig.get("threadId")
        except Exception:
            pass
    try:
        result = service.users().messages().send(userId="me", body=send_body).execute()
        return {"id": result.get("id"), "thread_id": result.get("threadId"), "account": label}
    except Exception as e:
        print(f"[email] Error sending: {e}")
        return None


def draft_email(to, subject, body, account=None):
    svc = _get_service_for_account(account)
    if not svc:
        return None
    label, service = svc
    message = MIMEText(body)
    message["To"] = to
    message["Subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    try:
        result = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
        return {"id": result.get("id"), "message_id": result.get("message", {}).get("id"), "account": label}
    except Exception as e:
        print(f"[email] Error creating draft: {e}")
        return None


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def format_emails_for_context(emails):
    if not emails:
        return "(no emails)"
    lines = []
    for e in emails:
        sender = e.get("from", "")
        if "<" in sender:
            sender = sender.split("<")[0].strip().strip('"')
        unread = " [UNREAD]" if e.get("is_unread") else ""
        account = f" [{e['account']}]" if e.get("account") else ""
        mid = f" [id:{e['id']}]" if e.get("id") else ""
        tid = f" [thread:{e['thread_id']}]" if e.get("thread_id") else ""
        line = f"- From: {sender} | Subject: {e.get('subject', '')}{unread}{account}{mid}{tid}"
        if e.get("snippet"):
            line += f"\n  {e['snippet'][:150]}"
        lines.append(line)
    return "\n".join(lines)


def format_thread_for_context(thread_data):
    if not thread_data or not thread_data.get("messages"):
        return "(empty thread)"
    lines = [f"Thread [{thread_data['account']}]:"]
    for msg in thread_data["messages"]:
        sender = msg.get("from", "")
        if "<" in sender:
            sender = sender.split("<")[0].strip().strip('"')
        lines.append(f"\n--- {sender} | {msg.get('date', '')} ---")
        lines.append(msg.get("body", "(no body)")[:2000])
    return "\n".join(lines)


if __name__ == "__main__":
    print("Gmail Integration Test")
    print("=" * 45)
    counts = get_unread_count()
    for l, c in counts.items():
        print(f"  {l}: {c} unread")
    emails = get_recent_emails(max_results=3, hours_back=24)
    if emails:
        print(f"\nRecent ({len(emails)}):")
        print(format_emails_for_context(emails))
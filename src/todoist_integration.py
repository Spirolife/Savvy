"""
Todoist integration — read/write, via the unified Todoist API v1.

Base URL is https://api.todoist.com/api/v1 (the older REST v2 and Sync v9
endpoints were sunset in early 2026 — don't reintroduce them).

Auth is a personal API token, read from the TODOIST_API_TOKEN env var or the
`todoist_api_token` key in credentials/config.json. Importing this module
raises if no token is configured, which is what makes tools.py drop the
Todoist tools from the registry instead of exposing tools that always fail.

Priority convention: the Todoist API numbers priority 4 (urgent) down to
1 (normal), which is the reverse of the p1..p4 labels shown in the app and
used by humans. Everything crossing this module's public boundary speaks
p1..p4; the inversion is confined to _to_api_priority/_to_ui_priority.

Due dates are deliberately NOT exposed — not read, not written, not shown.
These projects are a pool of things to pick up in spare time, not a dated
schedule, and a near-empty "due today" view was actively misleading. Tasks
carry a `due` field in the API payload; this module ignores it on purpose.
Don't add it back without asking.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import httpx

from paths import CONFIG_PATH

BASE_URL = "https://api.todoist.com/api/v1"
TIMEOUT = 20.0

# Todoist caps page size at 200 for these collection endpoints.
PAGE_LIMIT = 200

# by_completion_date accepts a window of at most 3 months.
MAX_COMPLETED_WINDOW_DAYS = 90


class TodoistError(RuntimeError):
    """An API call failed. Message is surfaced to the model as the tool result."""


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def _load_token() -> str:
    token = os.environ.get("TODOIST_API_TOKEN", "").strip()
    if token:
        return token
    try:
        with open(CONFIG_PATH) as f:
            token = (json.load(f).get("todoist_api_token") or "").strip()
    except (OSError, json.JSONDecodeError):
        token = ""
    if not token:
        raise TodoistError(
            "No Todoist API token. Set TODOIST_API_TOKEN or add 'todoist_api_token' "
            "to credentials/config.json (get one at Todoist → Settings → Integrations "
            "→ Developer)."
        )
    return token


# Fail at import time when unconfigured so tools.py skips the Todoist tools.
_TOKEN = _load_token()

_HEADERS = {
    "Authorization": f"Bearer {_TOKEN}",
    "Content-Type": "application/json",
}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------
def _request(method: str, path: str, *, params: dict | None = None, body: dict | None = None):
    """Issue one API call. Returns parsed JSON, or None for empty 204 bodies."""
    url = f"{BASE_URL}{path}"
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    clean_body = {k: v for k, v in (body or {}).items() if v is not None}
    try:
        resp = httpx.request(
            method, url, headers=_HEADERS, timeout=TIMEOUT,
            params=clean_params or None,
            json=clean_body if method in ("POST", "PUT") else None,
        )
    except httpx.RequestError as e:
        raise TodoistError(f"Could not reach Todoist: {e}") from e

    if resp.status_code == 401:
        raise TodoistError("Todoist rejected the API token (401). Check todoist_api_token.")
    if resp.status_code == 404:
        raise TodoistError(f"Not found: {method} {path} — the id may be wrong or already deleted.")
    if resp.status_code == 429:
        raise TodoistError("Todoist rate limit hit (429). Wait a moment and retry.")
    if resp.status_code >= 400:
        raise TodoistError(f"Todoist API {resp.status_code} on {method} {path}: {resp.text[:300]}")

    if resp.status_code == 204 or not resp.content:
        return None
    try:
        return resp.json()
    except ValueError as e:
        raise TodoistError(f"Todoist returned non-JSON on {method} {path}") from e


def _get_paginated(path: str, params: dict | None = None, max_items: int = 500) -> list[dict]:
    """Follow next_cursor until exhausted or max_items collected.

    v1 collection endpoints wrap results as {"results": [...], "next_cursor": ...},
    but a few return a bare list; handle both.
    """
    out: list[dict] = []
    cursor = None
    while True:
        page_params = dict(params or {})
        page_params["limit"] = min(PAGE_LIMIT, max_items - len(out))
        if cursor:
            page_params["cursor"] = cursor
        data = _request("GET", path, params=page_params)

        if isinstance(data, list):
            return data[:max_items]
        if not isinstance(data, dict):
            return out

        out.extend(data.get("results") or [])
        cursor = data.get("next_cursor")
        if not cursor or len(out) >= max_items:
            return out[:max_items]


# ---------------------------------------------------------------------------
# Priority helpers
# ---------------------------------------------------------------------------
def _to_api_priority(priority) -> int | None:
    """'p1'..'p4' (or 1..4 as the user's labels) -> Todoist's inverted 4..1."""
    if priority is None:
        return None
    s = str(priority).strip().lower().lstrip("p")
    if s not in ("1", "2", "3", "4"):
        raise TodoistError(f"priority must be p1, p2, p3 or p4 (got {priority!r})")
    return 5 - int(s)


def _to_ui_priority(api_priority) -> str:
    try:
        n = int(api_priority)
    except (TypeError, ValueError):
        return "p4"
    return f"p{5 - n}" if 1 <= n <= 4 else "p4"


# ---------------------------------------------------------------------------
# Projects  (Todoist's equivalent of a task list)
# ---------------------------------------------------------------------------
def list_projects() -> list[dict]:
    """All projects, inbox first."""
    projects = _get_paginated("/projects")
    return sorted(projects, key=lambda p: (not _is_inbox(p), (p.get("name") or "").lower()))


def _is_inbox(project: dict) -> bool:
    return bool(project.get("inbox_project") or project.get("is_inbox_project"))


def create_project(name: str, color: str | None = None, is_favorite: bool = False) -> dict:
    return _request("POST", "/projects", body={
        "name": name, "color": color, "is_favorite": is_favorite or None,
    })


def find_project_id(name: str | None) -> str | None:
    """Resolve a project name to an id. Case-insensitive, prefix match as fallback.

    None/empty means "no project filter" (or, on create, Todoist's Inbox default).
    """
    if not name:
        return None
    wanted = name.strip().lower()
    projects = list_projects()
    for p in projects:
        if (p.get("name") or "").lower() == wanted:
            return str(p["id"])
    if wanted in ("inbox",):
        for p in projects:
            if _is_inbox(p):
                return str(p["id"])
    matches = [p for p in projects if (p.get("name") or "").lower().startswith(wanted)]
    if len(matches) == 1:
        return str(matches[0]["id"])
    known = ", ".join(p.get("name", "?") for p in projects) or "(none)"
    raise TodoistError(f"No project matching {name!r}. Existing projects: {known}")


def _project_names() -> dict[str, str]:
    try:
        return {str(p["id"]): p.get("name", "") for p in list_projects()}
    except TodoistError:
        return {}


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
def list_labels() -> list[dict]:
    return _get_paginated("/labels")


# ---------------------------------------------------------------------------
# Tasks — read
# ---------------------------------------------------------------------------
def list_tasks(
    task_list: str | None = None,
    filter_query: str | None = None,
    label: str | None = None,
    max_results: int = 100,
) -> list[dict]:
    """Active (incomplete) tasks.

    task_list:    project name to scope to.
    filter_query: a Todoist filter expression ("#Personal Projects & p1",
                  "@errand", "search: kitchen"). Takes precedence — Todoist
                  evaluates it server-side. Date filters work but are pointless
                  here; these projects are undated by design.
    """
    if filter_query:
        tasks = _get_paginated(
            "/tasks/filter", {"query": filter_query}, max_items=max_results
        )
    else:
        params = {"label": label}
        if task_list:
            params["project_id"] = find_project_id(task_list)
        tasks = _get_paginated("/tasks", params, max_items=max_results)
    return _sort_tasks(tasks)


def _sort_tasks(tasks: list[dict]) -> list[dict]:
    """Urgent first, then alphabetical. No date ordering — see module docstring."""
    def key(t):
        return (-int(t.get("priority") or 1), (t.get("content") or "").lower())
    return sorted(tasks, key=key)


def get_task(task_id: str) -> dict:
    return _request("GET", f"/tasks/{task_id}")


def get_completed_tasks(since: str | None = None, until: str | None = None,
                        max_results: int = 100) -> list[dict]:
    """Tasks completed in a window (defaults to the last 7 days).

    `since`/`until` are YYYY-MM-DD or full ISO datetimes. Todoist requires both
    and allows at most a 3-month span.
    """
    now = datetime.now(timezone.utc)
    until_dt = _parse_window_bound(until, now)
    since_dt = _parse_window_bound(since, until_dt - timedelta(days=7))
    if since_dt > until_dt:
        since_dt, until_dt = until_dt, since_dt
    if (until_dt - since_dt).days > MAX_COMPLETED_WINDOW_DAYS:
        raise TodoistError(
            f"Completed-task window is capped at {MAX_COMPLETED_WINDOW_DAYS} days."
        )
    tasks = _get_paginated("/tasks/completed/by_completion_date", {
        "since": since_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "until": until_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, max_items=max_results)
    return sorted(tasks, key=lambda t: t.get("completed_at") or "", reverse=True)


def _parse_window_bound(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as e:
        raise TodoistError(f"Bad date {value!r} — use YYYY-MM-DD or an ISO datetime.") from e
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Tasks — write
# ---------------------------------------------------------------------------
def create_task(
    title: str,
    task_list: str | None = None,
    notes: str = "",
    priority=None,
    labels: list[str] | None = None,
    parent_id: str | None = None,
) -> dict:
    """Create a task. No due date is ever set — see module docstring."""
    return _request("POST", "/tasks", body={
        "content": title,
        "description": notes or None,
        "project_id": find_project_id(task_list),
        "parent_id": parent_id,
        "priority": _to_api_priority(priority),
        "labels": labels or None,
    })


def update_task(
    task_id: str,
    title: str | None = None,
    notes: str | None = None,
    priority=None,
    labels: list[str] | None = None,
) -> dict:
    """Edit a task in place. Due dates are not touched — see module docstring."""
    body = {
        "content": title,
        "description": notes,
        "priority": _to_api_priority(priority),
        "labels": labels,
    }
    if not any(v is not None for v in body.values()):
        raise TodoistError("update_task needs at least one field to change.")
    return _request("POST", f"/tasks/{task_id}", body=body)


def move_task(task_id: str, task_list: str) -> dict:
    """Move a task to a different project."""
    return _request("POST", f"/tasks/{task_id}", body={
        "project_id": find_project_id(task_list),
    })


def complete_task(task_id: str) -> bool:
    _request("POST", f"/tasks/{task_id}/close")
    return True


def reopen_task(task_id: str) -> bool:
    _request("POST", f"/tasks/{task_id}/reopen")
    return True


def delete_task(task_id: str) -> bool:
    _request("DELETE", f"/tasks/{task_id}")
    return True


# ---------------------------------------------------------------------------
# Formatting for model context
# ---------------------------------------------------------------------------
def format_tasks_for_context(tasks: list[dict], show_project: bool = True) -> str:
    """One line per task, carrying the task_id the model needs to act on it.

    Mirrors format_events_for_context: never guess an id, read it from here.
    """
    if not tasks:
        return "(no tasks)"
    names = _project_names() if show_project else {}
    lines = []
    for t in tasks:
        line = f"- {t.get('content', '(untitled)')}"
        prio = _to_ui_priority(t.get("priority"))
        if prio != "p4":
            line += f" [{prio}]"
        if t.get("labels"):
            line += " " + " ".join(f"@{lbl}" for lbl in t["labels"])
        project = names.get(str(t.get("project_id")))
        if project:
            line += f" (#{project})"
        if t.get("completed_at"):
            line += f" ✓ completed {t['completed_at'][:10]}"
        if t.get("id"):
            line += f" [task_id:{t['id']}]"
        lines.append(line)
    return "\n".join(lines)


def format_projects_for_context(projects: list[dict]) -> str:
    if not projects:
        return "(no projects)"
    lines = []
    for p in projects:
        line = f"- {p.get('name', '?')}"
        if _is_inbox(p):
            line += " (inbox)"
        if p.get("is_favorite"):
            line += " ★"
        line += f" [project_id:{p.get('id')}]"
        lines.append(line)
    return "\n".join(lines)

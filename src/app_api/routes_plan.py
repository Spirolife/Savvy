"""Plan, goal progress, meals, project tasks. OWNER: today-plan-diary agent."""

import logging
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app_api.auth import require_device
from app_api.routes_today import task_json
from app_api.server import ApiError

router = APIRouter(prefix="/v1", dependencies=[Depends(require_device)])
log = logging.getLogger("savvy.app_api.plan")


def _goals():
    import goals
    return goals


def _meals():
    import meals
    return meals


def _todoist():
    import todoist_integration
    return todoist_integration


def goal_json(g: dict) -> dict:
    latest = (g.get("progress") or [None])[0]
    note = None
    if latest:
        note = latest.get("note") or ""
        if latest.get("value"):
            note = f"{note} ({latest['value']})" if note else latest["value"]
    return {
        "id": g["id"],
        "title": g.get("title") or "",
        "category": g.get("category") or None,
        "target": g.get("target") or None,
        "status": g.get("status") or "active",
        "progress_note": note,
        "last_logged": datetime.fromtimestamp(latest["timestamp"]).astimezone().isoformat() if latest else None,
    }


def meal_json(m: dict) -> dict:
    return {
        "id": m["id"],
        "main": m.get("main") or "",
        "kind": m.get("kind") or "meal",
        "sides": list(m.get("sides") or []),
        "day": (m.get("day") or "")[:3] or None,
    }


def _plan_goals() -> list[dict]:
    try:
        return [goal_json(g) for g in _goals().list_goals("active", with_progress=1)]
    except Exception as e:
        log.warning("goals unavailable: %s", e)
        return []


def _plan_meals() -> dict:
    try:
        m = _meals()
        return {"week": m.week_start("this"), "diet": m.active_diet() or None,
                "items": [meal_json(x) for x in m.get_week("this")]}
    except Exception as e:
        log.warning("meals unavailable: %s", e)
        return {"week": None, "diet": None, "items": []}


def _plan_projects() -> list[dict]:
    try:
        td = _todoist()
        projects = td.list_projects()
        counts = Counter(str(t.get("project_id")) for t in td.list_tasks(max_results=500))
        return [{"id": str(p["id"]), "name": p.get("name") or "", "open_count": counts.get(str(p["id"]), 0)}
                for p in projects]
    except Exception as e:
        log.warning("todoist unavailable: %s", e)
        return []


@router.get("/plan")
def plan():
    return {"goals": _plan_goals(), "meals": _plan_meals(), "projects": _plan_projects()}


class Progress(BaseModel):
    note: str
    value: str = ""


@router.post("/goals/{goal_id}/progress")
def log_progress(goal_id: int, body: Progress):
    if not body.note.strip():
        raise ApiError(400, "bad_request", "Say what progress you made.")
    try:
        _goals().log_progress(goal_id, body.note, body.value)
    except ValueError as e:
        raise ApiError(404, "not_found", str(e))
    return {"ok": True}


class NewMeal(BaseModel):
    main: str
    kind: str = "meal"
    sides: list[str] = Field(default_factory=list)


@router.post("/meals")
def add_meal(body: NewMeal):
    """Plan a meal for this week, with the same weekly limits the model gets."""
    m = _meals()
    try:
        r = m.add_meal(body.main, body.kind, body.sides, week="this")
    except ValueError as e:
        raise ApiError(400, "meal_rule", str(e))
    if not r.get("success"):
        # The module words this for the model; drop the instruction to it.
        msg = (r.get("message") or "Not added.").replace(" Tell the user before adding more.", "")
        if r.get("next_week_with_room"):
            msg += f" The week of {r['next_week_with_room']} has room."
        raise ApiError(400, "meal_rule", msg)
    return {"ok": True, "id": r["meal"]["id"], "message": r.get("warning")}


@router.delete("/meals/{meal_id}")
def delete_meal(meal_id: int):
    try:
        _meals().remove_meal(meal_id)
    except ValueError as e:
        raise ApiError(404, "not_found", str(e))
    return {"ok": True}


@router.get("/projects/{project_id}/tasks")
def project_tasks(project_id: str):
    try:
        td = _todoist()
    except Exception as e:
        raise ApiError(503, "todoist_unavailable", f"Todoist isn't set up: {e}")
    try:
        names = {str(p["id"]): p.get("name", "") for p in td.list_projects()}
        tasks = td._sort_tasks(td._get_paginated("/tasks", {"project_id": project_id}))
    except Exception as e:
        raise ApiError(502, "todoist_error", f"Todoist didn't answer: {e}")
    return [task_json(t, names) for t in tasks]

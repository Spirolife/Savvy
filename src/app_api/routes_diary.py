"""Diary entries and notes. OWNER: today-plan-diary agent.

Diary entries are stored exactly as the other surfaces store them
(diary.store_entry): a morning plan is 'bod', an evening reflection 'eod', a
bad day 'bad_day' (as log_bad_day writes it), and a general entry 'note' (as
store_diary_entry writes it), which the app calls "entry".

Notes are the files in notes/ that save_note / read_note / list_notes use.
"""

import time
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

import paths
from app_api.auth import require_device
from app_api.server import ApiError

router = APIRouter(prefix="/v1", dependencies=[Depends(require_device)])

TYPES = {"bod": "bod", "eod": "eod", "bad_day": "bad_day", "entry": "note"}   # app → stored
MAX_NOTE_CHARS = 200_000


def _diary():
    import diary
    return diary


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat()


@router.get("/diary")
def list_diary(days: int = Query(default=14, ge=1, le=366)):
    db = _diary()._get_db()      # diary.py's reads don't return ids
    try:
        rows = db.execute(
            "SELECT id, entry_type, content, timestamp FROM diary WHERE timestamp > ? "
            "ORDER BY timestamp DESC, id DESC", (time.time() - days * 86400,)).fetchall()
    finally:
        db.close()
    stored = {v: k for k, v in TYPES.items()}
    return [{"id": str(i), "type": stored.get(t, "entry"), "text": c, "ts": _iso(ts)} for i, t, c, ts in rows]


class NewEntry(BaseModel):
    type: str
    text: str


@router.post("/diary")
def add_diary(body: NewEntry):
    if body.type not in TYPES:
        raise ApiError(400, "bad_type", f"type must be one of {', '.join(TYPES)}.")
    text = body.text.strip()
    if not text:
        raise ApiError(400, "empty", "Write something first.")
    _diary().store_entry(TYPES[body.type], text)
    return {"ok": True}


def _note_path(name: str):
    """The file for `name`, only if it's a plain file directly inside notes/."""
    if (not name or name in (".", "..") or name.startswith(".") or "/" in name or "\\" in name
            or "\x00" in name):
        raise ApiError(400, "bad_name", "Not a note name.")
    root = paths.NOTES_DIR.resolve()
    fp = (root / name).resolve()
    if fp.parent != root:
        raise ApiError(400, "bad_name", "Not a note name.")
    return fp


@router.get("/notes")
def list_notes():
    root = paths.NOTES_DIR
    if not root.is_dir():
        return []
    files = [f for f in root.iterdir() if f.is_file() and not f.name.startswith(".")]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.name, "updated": _iso(f.stat().st_mtime)} for f in files]


@router.get("/notes/{name}")
def read_note(name: str):
    fp = _note_path(name)
    if not fp.is_file():
        raise ApiError(404, "not_found", f"No note named {name!r}.")
    try:
        text = fp.read_text(errors="replace")
    except OSError as e:
        raise ApiError(500, "read_failed", str(e))
    return {"name": fp.name, "text": text[:MAX_NOTE_CHARS]}

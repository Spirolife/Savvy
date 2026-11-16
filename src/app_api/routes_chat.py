"""Chat stream (sse), confirm, history. OWNER: chat-inbox agent.

One turn runs on its own thread through core.respond, exactly like the desktop
window's ChatWorker; its callbacks are bridged to the HTTP response through an
asyncio queue and sent as server-sent events (docs/app-api.md, "Chat").
"""

import asyncio
import itertools
import json
import threading

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import core
import tool_diagnostics
from app_api.auth import require_device
from app_api.server import ApiError

router = APIRouter(prefix="/v1", dependencies=[Depends(require_device)])

CONFIRM_TIMEOUT = 300.0     # seconds an unanswered confirmation waits before it is denied
KEEPALIVE = 15.0            # seconds between sse comments while nothing else is sent
SESSION_ID = "app"

_turn_lock = threading.Lock()           # one turn at a time
_pending: dict[str, "_Pending"] = {}    # confirm id -> waiting confirmation
_pending_lock = threading.Lock()
_ids = itertools.count(1)
_client = None
_client_lock = threading.Lock()


def get_client():
    """The Anthropic client, created on first use (no key needed to start the server)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = core.get_client()
        return _client


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def describe(name: str, result) -> dict:
    """{"name", "state": read|done|denied|error, "detail", "warning"} for a tool event."""
    fn = getattr(tool_diagnostics, "describe_tool_result", None)
    d = None
    if fn is not None:
        try:
            d = fn(name, result)
        except Exception:
            d = None
    if not isinstance(d, dict):
        d = _describe_fallback(name, result)
    state = d.get("state") or "done"
    if state == "failed":
        state = "error"
    return {"name": name, "state": state, "detail": d.get("detail") or "",
            "warning": d.get("warning") or None}


def _describe_fallback(name: str, result) -> dict:
    """Same rules as tool_diagnostics.log_tool_result, for builds without describe_tool_result."""
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    short = tool_diagnostics._short
    if isinstance(parsed, dict) and parsed.get("denied"):
        return {"state": "denied", "detail": "you denied this call"}
    if isinstance(parsed, dict) and (parsed.get("success") is False or "error" in parsed):
        why = parsed.get("error") or parsed.get("reason") or parsed.get("message") or "failed"
        return {"state": "error", "detail": short(why, 160)}
    if tool_diagnostics.get_danger_level(name) == 0:
        if isinstance(parsed, (dict, list)):
            detail = f"{len(parsed)} item(s)" if isinstance(parsed, list) else "ok"
        else:
            n = len(str(result).strip().splitlines())
            detail = short(result, 60) if n == 1 else f"{n} lines"
        return {"state": "read", "detail": detail}
    detail = tool_diagnostics._summarize(name, parsed) if isinstance(parsed, dict) else short(result)
    warning = short(parsed["warning"], 120) if isinstance(parsed, dict) and parsed.get("warning") else None
    return {"state": "done", "detail": detail, "warning": warning}


class _Pending:
    def __init__(self, turn: "_Turn"):
        self.turn = turn
        self.decided = threading.Event()
        self.decision = "deny"


class _Turn:
    """One running turn: emits events to the response and answers the tool gate."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue()
        self.gone = False       # the client disconnected; nothing more is delivered

    def emit(self, event: str, data: dict) -> None:
        if self.gone:
            return
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, (event, data))
        except RuntimeError:    # the loop is closed: the response is long gone
            self.gone = True

    def tool_event(self, name: str, _inp, result) -> None:
        self.emit("tool", describe(name, result))

    def ask(self, name: str, inp: dict, _call_id: str, level: int) -> str:
        """The tool gate's prompt handler: runs on the turn's thread and blocks."""
        if self.gone:
            return "deny"
        cid = f"c{next(_ids)}"
        pending = _Pending(self)
        with _pending_lock:
            _pending[cid] = pending
        try:
            args = {k: v for k, v in (inp or {}).items() if k != "_importance"}
            self.emit("confirm", {"id": cid, "name": name, "level": level,
                                  "args": json.loads(json.dumps(args, default=str))})
            pending.decided.wait(CONFIRM_TIMEOUT)
            return "approve" if pending.decided.is_set() and pending.decision == "allow" else "deny"
        finally:
            with _pending_lock:
                _pending.pop(cid, None)

    def abandon(self) -> None:
        """The client went away: deny whatever it was asked, let the turn finish."""
        self.gone = True
        with _pending_lock:
            for p in _pending.values():
                if p.turn is self:
                    p.decision = "deny"
                    p.decided.set()


def _run_turn(text: str, turn: _Turn) -> None:
    memory = None
    try:
        signin_reply = core.finish_pasted_signin(text, markdown=True)
        if signin_reply:        # holds a one-time code: not for the model or memory
            turn.emit("done", {"reply": signin_reply})
            return
        from memory import Memory
        client = get_client()
        memory = Memory(session_id=SESSION_ID)
        previous = tool_diagnostics._prompt_handler
        tool_diagnostics.set_prompt_handler(turn.ask)
        try:
            reply, _, _ = core.respond(
                client, memory, text, surface="app",
                stream_callback=lambda delta: turn.emit("text", {"delta": delta}),
                tool_event_callback=turn.tool_event,
            )
        finally:
            tool_diagnostics.set_prompt_handler(previous)
        turn.emit("done", {"reply": reply})
    except Exception as e:
        print(f"[app_api] Chat turn failed: {type(e).__name__}: {e}")
        turn.emit("error", {"message": str(e) or type(e).__name__})
    finally:
        db = getattr(memory, "db", None)
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
        _turn_lock.release()


class ChatBody(BaseModel):
    message: str


class ConfirmBody(BaseModel):
    id: str
    decision: str


@router.post("/chat")
async def chat(body: ChatBody, request: Request):
    text = body.message.strip()
    if not text:
        raise ApiError(400, "empty", "The message is empty.")
    if not _turn_lock.acquire(blocking=False):
        raise ApiError(409, "busy", "Savvy is still answering the last message.")
    turn = _Turn(asyncio.get_running_loop())
    try:
        threading.Thread(target=_run_turn, args=(text, turn), daemon=True, name="app-chat").start()
    except Exception:
        _turn_lock.release()
        raise

    async def stream():
        try:
            while True:
                try:
                    event, data = await asyncio.wait_for(turn.queue.get(), KEEPALIVE)
                except (asyncio.TimeoutError, TimeoutError):
                    yield ": keepalive\n\n"
                    continue
                yield sse(event, data)
                if event in ("done", "error"):
                    return
        finally:
            turn.abandon()      # no-op once finished; denies pending confirms on disconnect

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chat/confirm")
def confirm(body: ConfirmBody):
    if body.decision not in ("allow", "deny"):
        raise ApiError(400, "bad_decision", "decision must be 'allow' or 'deny'.")
    with _pending_lock:
        pending = _pending.get(body.id)
        if pending is None:
            raise ApiError(404, "not_waiting", "Nothing is waiting for that answer.")
        pending.decision = body.decision
        pending.decided.set()
    return {"ok": True}


_SURFACES = [("[Signal] ", "signal"), ("[App] ", "app")]


def history_item(role: str, content: str, ts) -> dict:
    surface = "desktop"
    for tag, name in _SURFACES:
        if content.startswith(tag):
            surface, content = name, content[len(tag):]
            break
    return {"role": role, "content": content, "ts": iso_ts(ts), "surface": surface}


def iso_ts(ts) -> str | None:
    from datetime import datetime
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


@router.get("/chat/history")
def history(limit: int = 40):
    from memory import Memory
    limit = max(1, min(limit, 200))
    memory = Memory(session_id=SESSION_ID)
    try:
        rows = memory.retrieve_recent(n=limit)
    finally:
        memory.db.close()
    return [history_item(r["role"], r["content"], r["timestamp"]) for r in rows
            if r.get("role") in ("user", "assistant")]

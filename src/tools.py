"""
Comprehensive tool definitions and execution — 35+ tools.
Calendar, Tasks, Email, Diary, Notes.

Every tool's input schema includes an optional `_importance` field. The LLM
should set it to "high" for unusual or destructive level-1 calls (e.g.
deleting a recurring meeting) — that escalation triggers a human prompt
that wouldn't otherwise happen. The flag has no effect on level 0 or 2
calls (level 0 always runs, level 2 always prompts).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from diary import store_entry
from tool_diagnostics import (
	review_tool_call, log_tool_result, log_tool_error, log_denied,
)


def _json_default(o):
	if isinstance(o, datetime):
		return o.isoformat()
	raise TypeError(f"Not serializable: {type(o).__name__}")


def _dumps(obj) -> str:
	return json.dumps(obj, default=_json_default)


def _strip_meta(inp: dict) -> dict:
	"""Remove diagnostics-only fields before passing to the underlying integration."""
	return {k: v for k, v in inp.items() if not k.startswith("_")}


CALENDAR_AVAILABLE = False
try:
	from calendar_integration import (
		create_event, create_allday_event, delete_event, move_event,
		quick_add_event, check_freebusy, delete_calendar,
		list_calendars, get_today_events, get_week_events,
		_fetch_events, find_calendar_id, _get_services,
		format_events_for_context, format_calendars_for_context,
	)
	CALENDAR_AVAILABLE = True
except Exception as e:
	print(f"[tools] Calendar: {e}")

TASKS_AVAILABLE = False
try:
	from tasks_integration import (
		list_task_lists, create_task_list, list_tasks,
		create_task, complete_task, delete_task,
		format_tasks_for_context,
	)
	TASKS_AVAILABLE = True
except Exception as e:
	print(f"[tools] Tasks: {e}")

EMAIL_AVAILABLE = False
try:
	from email_integration import (
		send_email, draft_email, search_emails, get_recent_emails,
		read_full_email, read_thread, modify_email,
		star_email, archive_email, mark_read, mark_unread, trash_email,
		list_labels, create_label,
		list_drafts, send_draft, delete_draft,
		format_emails_for_context, format_thread_for_context,
	)
	EMAIL_AVAILABLE = True
except Exception as e:
	print(f"[tools] Email: {e}")

try:
	from paths import PROJECT_ROOT
	NOTES_DIR = PROJECT_ROOT / "notes"
except ImportError:
	NOTES_DIR = Path(__file__).parent.parent / "notes"
NOTES_DIR.mkdir(exist_ok=True)


# =====================================================================
# TOOL DEFINITIONS
# =====================================================================
# Description appended to every tool so the LLM knows about the escalation flag.
_IMPORTANCE_NOTE = (
	" Set _importance='high' if this call is unusual, destructive, or "
	"could surprise the user (e.g. deleting recurring events, bulk changes)."
)
_IMPORTANCE_PROP = {
	"_importance": {
		"type": "string",
		"enum": ["normal", "high"],
		"description": "Flag unusual/destructive calls as 'high' to request human confirmation.",
	}
}


def _add_importance(schema: dict) -> dict:
	"""Inject the optional _importance field into a tool's input_schema."""
	schema = dict(schema)
	props = dict(schema.get("properties", {}))
	props.update(_IMPORTANCE_PROP)
	schema["properties"] = props
	return schema


TOOLS = []


def _add_tool(t: dict) -> dict:
	t["description"] = t.get("description", "") + _IMPORTANCE_NOTE
	t["input_schema"] = _add_importance(t["input_schema"])
	return t


# ======================== CALENDAR (11 tools) ========================
if CALENDAR_AVAILABLE:
	TOOLS.extend([_add_tool(t) for t in [
		{"name": "create_calendar_event", "description": "Create a timed event.", "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}, "start_time": {"type": "string", "description": "ISO datetime e.g. '2026-03-25T14:00:00-04:00'"}, "end_time": {"type": "string"}, "calendar_name": {"type": "string", "description": "Sub-calendar name. Omit for primary."}, "account": {"type": "string"}, "description": {"type": "string"}, "location": {"type": "string"}}, "required": ["summary", "start_time", "end_time"]}},
		{"name": "create_allday_event", "description": "Create an all-day event.", "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD"}, "calendar_name": {"type": "string"}, "account": {"type": "string"}, "description": {"type": "string"}}, "required": ["summary", "date"]}},
		{"name": "quick_add_event", "description": "Create event from natural language. Google parses the text.", "input_schema": {"type": "object", "properties": {"text": {"type": "string"}, "calendar_name": {"type": "string"}, "account": {"type": "string"}}, "required": ["text"]}},
		{"name": "delete_calendar_event", "description": "Delete an event by ID.", "input_schema": {"type": "object", "properties": {"event_id": {"type": "string"}, "calendar_id": {"type": "string", "description": "Defaults to primary"}, "account": {"type": "string"}}, "required": ["event_id"]}},
		{"name": "update_calendar_event", "description": "Update event fields (title, time, description, location).", "input_schema": {"type": "object", "properties": {"event_id": {"type": "string"}, "calendar_id": {"type": "string"}, "account": {"type": "string"}, "summary": {"type": "string"}, "start_time": {"type": "string"}, "end_time": {"type": "string"}, "description": {"type": "string"}, "location": {"type": "string"}}, "required": ["event_id"]}},
		{"name": "move_event", "description": "Move an event from one calendar to another.", "input_schema": {"type": "object", "properties": {"event_id": {"type": "string"}, "source_calendar": {"type": "string"}, "dest_calendar": {"type": "string"}, "account": {"type": "string"}}, "required": ["event_id", "source_calendar", "dest_calendar"]}},
		{"name": "create_calendar", "description": "Create a new sub-calendar category.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "account": {"type": "string"}}, "required": ["name"]}},
		{"name": "delete_calendar", "description": "Delete a sub-calendar. Cannot delete primary.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "account": {"type": "string"}}, "required": ["name"]}},
		{"name": "list_calendars", "description": "List all sub-calendars.", "input_schema": {"type": "object", "properties": {}}},
		{"name": "get_calendar_range", "description": "Get events for any date range.", "input_schema": {"type": "object", "properties": {"start_date": {"type": "string"}, "end_date": {"type": "string"}}, "required": ["start_date", "end_date"]}},
		{"name": "check_freebusy", "description": "Find free/busy time slots in a date range.", "input_schema": {"type": "object", "properties": {"start_date": {"type": "string"}, "end_date": {"type": "string"}, "account": {"type": "string"}}, "required": ["start_date", "end_date"]}},
	]])

# ======================== TASKS (6 tools) ========================
if TASKS_AVAILABLE:
	TOOLS.extend([_add_tool(t) for t in [
		{"name": "create_task", "description": "Create a to-do task.", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "task_list": {"type": "string"}, "notes": {"type": "string"}, "due_date": {"type": "string"}}, "required": ["title"]}},
		{"name": "list_tasks", "description": "Show tasks from a list.", "input_schema": {"type": "object", "properties": {"task_list": {"type": "string"}, "show_completed": {"type": "boolean"}}}},
		{"name": "complete_task", "description": "Mark a task done.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "task_list": {"type": "string"}}, "required": ["task_id"]}},
		{"name": "delete_task", "description": "Delete a task.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "task_list": {"type": "string"}}, "required": ["task_id"]}},
		{"name": "create_task_list", "description": "Create a new task list.", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
		{"name": "list_task_lists", "description": "Show all task lists.", "input_schema": {"type": "object", "properties": {}}},
	]])

# ======================== EMAIL (14 tools) ========================
if EMAIL_AVAILABLE:
	TOOLS.extend([_add_tool(t) for t in [
		{"name": "send_email", "description": "Send email immediately. Only if user says 'send'.", "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "account": {"type": "string"}, "cc": {"type": "string"}, "reply_to_id": {"type": "string"}}, "required": ["to", "subject", "body"]}},
		{"name": "draft_email", "description": "Create draft for review. Default for email requests.", "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "account": {"type": "string"}}, "required": ["to", "subject", "body"]}},
		{"name": "search_emails", "description": "Search emails with Gmail syntax.", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}},
		{"name": "read_full_email", "description": "Read the full body of a specific email by message ID.", "input_schema": {"type": "object", "properties": {"message_id": {"type": "string"}, "account": {"type": "string"}}, "required": ["message_id"]}},
		{"name": "read_thread", "description": "Read an entire email thread by thread ID.", "input_schema": {"type": "object", "properties": {"thread_id": {"type": "string"}, "account": {"type": "string"}}, "required": ["thread_id"]}},
		{"name": "get_recent_emails", "description": "Get recent emails from all accounts.", "input_schema": {"type": "object", "properties": {"hours_back": {"type": "integer"}, "max_results": {"type": "integer"}}}},
		{"name": "star_email", "description": "Star/flag an email.", "input_schema": {"type": "object", "properties": {"message_id": {"type": "string"}, "account": {"type": "string"}}, "required": ["message_id"]}},
		{"name": "archive_email", "description": "Archive an email (remove from inbox).", "input_schema": {"type": "object", "properties": {"message_id": {"type": "string"}, "account": {"type": "string"}}, "required": ["message_id"]}},
		{"name": "mark_email_read", "description": "Mark email as read.", "input_schema": {"type": "object", "properties": {"message_id": {"type": "string"}, "account": {"type": "string"}}, "required": ["message_id"]}},
		{"name": "trash_email", "description": "Move email to trash.", "input_schema": {"type": "object", "properties": {"message_id": {"type": "string"}, "account": {"type": "string"}}, "required": ["message_id"]}},
		{"name": "list_drafts", "description": "List email drafts.", "input_schema": {"type": "object", "properties": {"account": {"type": "string"}, "max_results": {"type": "integer"}}}},
		{"name": "send_draft", "description": "Send an existing draft.", "input_schema": {"type": "object", "properties": {"draft_id": {"type": "string"}, "account": {"type": "string"}}, "required": ["draft_id"]}},
		{"name": "list_email_labels", "description": "List all Gmail labels/folders.", "input_schema": {"type": "object", "properties": {"account": {"type": "string"}}}},
		{"name": "create_email_label", "description": "Create a new Gmail label.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "account": {"type": "string"}}, "required": ["name"]}},
	]])

# ======================== DIARY & NOTES (4 tools) ========================
TOOLS.extend([_add_tool(t) for t in [
	{"name": "store_diary_entry", "description": "Store diary entry: 'bod' morning, 'eod' evening, 'note' general.", "input_schema": {"type": "object", "properties": {"entry_type": {"type": "string", "enum": ["bod", "eod", "note"]}, "content": {"type": "string"}}, "required": ["entry_type", "content"]}},
	{"name": "save_note", "description": "Save a note to local file.", "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean"}}, "required": ["filename", "content"]}},
	{"name": "read_note", "description": "Read a saved note.", "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}},
	{"name": "list_notes", "description": "List all saved notes.", "input_schema": {"type": "object", "properties": {}}},
	# TODO: implement an explicit remember rule, even though it uses chat summaries to add rules thus far
	# {"name": "remember_rule", "description": "Save a persistent rule or preference the user wants you to follow in future conversations. Call this whenever the user says 'remember', 'save as a rule', 'always do X', 'never do Y', or similar. The fact will be re-surfaced in future sessions when relevant.", "input_schema": {"type": "object", "properties": {"rule": {"type": "string", "description": "The rule, in clear declarative form (e.g. 'Always fetch real event IDs from get_calendar_range before calling update_calendar_event or delete_calendar_event')"}}, "required": ["rule"]}},
]])


# =====================================================================
# EXECUTION
# =====================================================================
def _execute_tool_inner(name: str, inp: dict) -> str:
	"""Pure dispatch — _importance and other meta fields already stripped."""
	# ---- CALENDAR ----
	if name == "create_calendar_event":
		r = create_event(summary=inp["summary"], start_time=inp["start_time"], end_time=inp["end_time"], calendar_name=inp.get("calendar_name"), account=inp.get("account"), description=inp.get("description", ""), location=inp.get("location", ""))
		return _dumps({"success": bool(r), "event": r}) if r else _dumps({"success": False, "error": "Failed"})

	elif name == "create_allday_event":
		r = create_allday_event(summary=inp["summary"], date=inp["date"], calendar_name=inp.get("calendar_name"), account=inp.get("account"), description=inp.get("description", ""))
		return _dumps({"success": bool(r), "event": r}) if r else _dumps({"success": False})

	elif name == "quick_add_event":
		r = quick_add_event(text=inp["text"], calendar_name=inp.get("calendar_name"), account=inp.get("account"))
		return _dumps({"success": bool(r), "event": r}) if r else _dumps({"success": False})

	elif name == "delete_calendar_event":
		return _dumps({"success": delete_event(inp["event_id"], inp.get("calendar_id", "primary"), inp.get("account"))})

	elif name == "update_calendar_event":
		cal_id = inp.get("calendar_id", "primary")
		for label, service in _get_services():
			if inp.get("account") and label != inp["account"]:
				continue
			try:
				event = service.events().get(calendarId=cal_id, eventId=inp["event_id"]).execute()
				for f in ["summary", "description", "location"]:
					if f in inp: event[f] = inp[f]
				if "start_time" in inp: event["start"] = {"dateTime": inp["start_time"]}
				if "end_time" in inp: event["end"] = {"dateTime": inp["end_time"]}
				r = service.events().update(calendarId=cal_id, eventId=inp["event_id"], body=event).execute()
				return _dumps({"success": True, "summary": r.get("summary")})
			except Exception as e:
				return _dumps({"success": False, "error": str(e)})
		return _dumps({"success": False, "error": "No matching account"})

	elif name == "move_event":
		src = inp["source_calendar"]
		dst = inp["dest_calendar"]
		found_src = find_calendar_id(src, inp.get("account"))
		found_dst = find_calendar_id(dst, inp.get("account"))
		src_id = found_src[0] if found_src else src
		dst_id = found_dst[0] if found_dst else dst
		r = move_event(inp["event_id"], src_id, dst_id, inp.get("account"))
		return _dumps({"success": bool(r), "result": r}) if r else _dumps({"success": False})

	elif name == "create_calendar":
		for label, service in _get_services():
			if inp.get("account") and label != inp["account"]:
				continue
			try:
				r = service.calendars().insert(body={"summary": inp["name"]}).execute()
				return _dumps({"success": True, "id": r["id"], "name": inp["name"]})
			except Exception as e:
				return _dumps({"success": False, "error": str(e)})
		return _dumps({"success": False, "error": "No matching account"})

	elif name == "delete_calendar":
		return _dumps({"success": delete_calendar(inp["name"], inp.get("account"))})

	elif name == "list_calendars":
		cals = list_calendars()
		return _dumps({"calendars": [{"name": c["name"], "account": c["account"], "primary": c["primary"]} for c in cals]})

	elif name == "get_calendar_range":
		start = datetime(*map(int, inp["start_date"].split("-")), tzinfo=timezone.utc)
		end = datetime(*map(int, inp["end_date"].split("-")), hour=23, minute=59, second=59, tzinfo=timezone.utc)
		events = _fetch_events(start, end, max_results=100)
		return format_events_for_context(events) if events else f"No events {inp['start_date']} to {inp['end_date']}"

	elif name == "check_freebusy":
		busy = check_freebusy(inp["start_date"], inp["end_date"], inp.get("account"))
		if busy:
			lines = [f"Busy: {b['start']} - {b['end']}" for b in busy]
			return f"Busy periods:\n" + "\n".join(lines)
		return f"Completely free between {inp['start_date']} and {inp['end_date']}"

	# ---- TASKS ----
	elif name == "create_task":
		r = create_task(title=inp["title"], task_list=inp.get("task_list"), notes=inp.get("notes", ""), due_date=inp.get("due_date"))
		return _dumps({"success": bool(r), "task": r}) if r else _dumps({"success": False})

	elif name == "list_tasks":
		return format_tasks_for_context(list_tasks(task_list=inp.get("task_list"), show_completed=inp.get("show_completed", False)))

	elif name == "complete_task":
		return _dumps({"success": complete_task(inp["task_id"], inp.get("task_list"))})

	elif name == "delete_task":
		return _dumps({"success": delete_task(inp["task_id"], inp.get("task_list"))})

	elif name == "create_task_list":
		r = create_task_list(inp["title"])
		return _dumps({"success": bool(r), "list": r}) if r else _dumps({"success": False})

	elif name == "list_task_lists":
		return _dumps({"task_lists": list_task_lists()})

	# ---- EMAIL ----
	elif name == "send_email":
		r = send_email(to=inp["to"], subject=inp["subject"], body=inp["body"], account=inp.get("account"), cc=inp.get("cc", ""), reply_to_id=inp.get("reply_to_id"))
		return _dumps({"success": bool(r), "message": f"Sent to {inp['to']}"}) if r else _dumps({"success": False})

	elif name == "draft_email":
		r = draft_email(to=inp["to"], subject=inp["subject"], body=inp["body"], account=inp.get("account"))
		return _dumps({"success": bool(r), "message": f"Draft for {inp['to']}"}) if r else _dumps({"success": False})

	elif name == "search_emails":
		results = search_emails(query=inp["query"], max_results=inp.get("max_results", 5))
		return format_emails_for_context(results) if results else "(no matches)"

	elif name == "read_full_email":
		r = read_full_email(inp["message_id"], inp.get("account"))
		if r:
			return f"From: {r['from']}\nTo: {r['to']}\nCC: {r.get('cc','')}\nDate: {r['date']}\nSubject: {r['subject']}\n\n{r['body']}"
		return _dumps({"error": "Could not read email"})

	elif name == "read_thread":
		r = read_thread(inp["thread_id"], inp.get("account"))
		if r:
			return format_thread_for_context(r)
		return _dumps({"error": "Could not read thread"})

	elif name == "get_recent_emails":
		results = get_recent_emails(max_results=inp.get("max_results", 10), hours_back=inp.get("hours_back", 24))
		return format_emails_for_context(results) if results else "(no recent emails)"

	elif name == "star_email":
		return _dumps({"success": star_email(inp["message_id"], inp.get("account"))})

	elif name == "archive_email":
		return _dumps({"success": archive_email(inp["message_id"], inp.get("account"))})

	elif name == "mark_email_read":
		return _dumps({"success": mark_read(inp["message_id"], inp.get("account"))})

	elif name == "trash_email":
		return _dumps({"success": trash_email(inp["message_id"], inp.get("account"))})

	elif name == "list_drafts":
		drafts = list_drafts(max_results=inp.get("max_results", 10), account=inp.get("account"))
		if drafts:
			lines = [f"- To: {d['to']} | Subject: {d['subject']} [draft_id:{d['id']}]" for d in drafts]
			return "\n".join(lines)
		return "(no drafts)"

	elif name == "send_draft":
		r = send_draft(inp["draft_id"], inp.get("account"))
		return _dumps({"success": bool(r)}) if r else _dumps({"success": False})

	elif name == "list_email_labels":
		labels = list_labels(inp.get("account"))
		user_labels = [l for l in labels if l["type"] == "user"]
		system_labels = [l for l in labels if l["type"] == "system"]
		parts = []
		if user_labels:
			parts.append("Custom labels:\n" + "\n".join(f"- {l['name']} [id:{l['id']}]" for l in user_labels))
		if system_labels:
			parts.append("System labels:\n" + "\n".join(f"- {l['name']}" for l in system_labels[:10]))
		return "\n\n".join(parts) if parts else "(no labels)"

	elif name == "create_email_label":
		r = create_label(inp["name"], inp.get("account"))
		return _dumps({"success": bool(r), "label": r}) if r else _dumps({"success": False})

	# ---- DIARY ----
	elif name == "store_diary_entry":
		store_entry(entry_type=inp["entry_type"], content=inp["content"])
		return _dumps({"success": True, "type": inp["entry_type"]})

	# ---- NOTES ----
	elif name == "save_note":
		fp = NOTES_DIR / inp["filename"]
		mode = "a" if inp.get("append") else "w"
		prefix = "\n\n" if inp.get("append") and fp.exists() else ""
		with open(fp, mode) as f:
			f.write(prefix + inp["content"])
		return _dumps({"success": True, "file": inp["filename"]})

	elif name == "read_note":
		fp = NOTES_DIR / inp["filename"]
		return fp.read_text()[:5000] if fp.exists() else _dumps({"error": "Not found"})

	elif name == "list_notes":
		files = sorted(NOTES_DIR.glob("*"))
		return "\n".join(f"- {f.name} ({f.stat().st_size}b)" for f in files if f.is_file()) or "(no notes)"

	else:
		return _dumps({"error": f"Unknown tool: {name}"})


def execute_tool(name: str, inp: dict) -> str:
	"""Diagnostic-wrapped tool execution. Honors danger-level confirmation."""
	call_id, approved = review_tool_call(name, inp)
	if not approved:
		return log_denied(call_id, name)

	# Strip diagnostic-only fields before dispatching to the integration layer.
	clean_inp = _strip_meta(inp)
	try:
		result = _execute_tool_inner(name, clean_inp)
		log_tool_result(call_id, name, result)
		return result
	except Exception as e:
		log_tool_error(call_id, name, e)
		return _dumps({"error": str(e)})


# =====================================================================
# TOOL-USE CONVERSATION LOOP
# =====================================================================
def chat_with_tools(client, model, system, messages, max_tokens=1024, stream_callback=None):
	current_messages = list(messages)
	total_in = total_out = 0

	while True:
		resp = client.messages.create(model=model, max_tokens=max_tokens, system=system, messages=current_messages, tools=TOOLS if TOOLS else None)
		total_in += resp.usage.input_tokens
		total_out += resp.usage.output_tokens

		text_parts, tool_uses = [], []
		for block in resp.content:
			if block.type == "text":
				text_parts.append(block.text)
				if stream_callback:
					stream_callback(block.text)
			elif block.type == "tool_use":
				tool_uses.append(block)

		current_messages.append({"role": "assistant", "content": resp.content})

		if resp.stop_reason != "tool_use" or not tool_uses:
			return "".join(text_parts), total_in, total_out

		tool_results = []
		for tu in tool_uses:
			if stream_callback:
				stream_callback(f"\n[Using {tu.name}...]\n")
			result = execute_tool(tu.name, tu.input)
			tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result})

		current_messages.append({"role": "user", "content": tool_results})
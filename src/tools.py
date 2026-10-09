"""
Comprehensive tool definitions and execution — 35+ tools.
Calendar, Tasks, Email, Diary, Notes.

Level-1 tools (internal writes) take an optional `_importance` field. The LLM
should set it to "high" for unusual or destructive calls (e.g. deleting a
recurring meeting) — that escalation triggers a human prompt that wouldn't
otherwise happen. Level 0 and 2 tools don't get the field: it could not change
anything for them (level 0 always runs, level 2 always prompts).
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from diary import store_entry
from tool_diagnostics import (
	review_tool_call, log_tool_result, log_tool_error, log_denied, get_danger_level,
)
from task_history import log_task, check_history as _check_history_impl
from reminders import add_reminder, remove_reminder, update_reminder, list_pending as _list_reminders_impl


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
		quick_add_event, delete_calendar, resolve_write_account,
		list_calendars, get_today_events, get_week_events, get_range_events,
		_fetch_events, find_calendar_id, _get_services, resolve_event,
		format_events_for_context, format_calendars_for_context, user_tz,
	)
	CALENDAR_AVAILABLE = True
except Exception as e:
	print(f"[tools] Calendar: {e}")

TODOIST_AVAILABLE = False
try:
	from todoist_integration import (
		list_projects, create_project, list_labels as list_todoist_labels,
		list_tasks, get_completed_tasks, get_task,
		create_task, update_task, move_task,
		complete_task, reopen_task, delete_task,
		format_tasks_for_context, format_projects_for_context,
	)
	TODOIST_AVAILABLE = True
except Exception as e:
	print(f"[tools] Todoist: {e}")

EMAIL_AVAILABLE = False
try:
	from email_integration import (
		send_email, draft_email, search_emails, get_recent_emails,
		read_full_email, read_thread, modify_email,
		star_email, archive_email, mark_read, mark_unread, trash_email,
		list_labels as list_gmail_labels, create_label,
		list_drafts, send_draft, delete_draft,
		format_emails_for_context, format_thread_for_context,
	)
	EMAIL_AVAILABLE = True
except Exception as e:
	print(f"[tools] Email: {e}")

from paths import NOTES_DIR, add_savvy_rule

TRAVEL_AVAILABLE = False
try:
	import places as _places
	import travel as _travel
	TRAVEL_AVAILABLE = True
except Exception as e:
	print(f"[tools] Travel: {e}")


# =====================================================================
# TOOL DEFINITIONS
# =====================================================================
# The escalation flag, explained once in the field itself. It used to be added
# to all 52 tools plus a sentence appended to every description — about a third
# of the tool-definition tokens on every request, mostly on tools where the
# flag has no effect.
_IMPORTANCE_PROP = {
	"_importance": {
		"type": "string",
		"enum": ["normal", "high"],
		"description": "'high' makes the user confirm before this runs. Use it when the call is unusual, destructive, or could surprise them (e.g. deleting a recurring event, bulk changes).",
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
	if get_danger_level(t["name"]) == 1:
		t["input_schema"] = _add_importance(t["input_schema"])
	return t


# ======================== CALENDAR (11 tools) ========================
if CALENDAR_AVAILABLE:
	TOOLS.extend([_add_tool(t) for t in [
		{"name": "create_calendar_event", "description": "Create a timed event.", "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}, "start_time": {"type": "string", "description": "ISO datetime e.g. '2026-03-25T14:00:00-04:00'"}, "end_time": {"type": "string"}, "calendar_name": {"type": "string", "description": "Sub-calendar name, e.g. Appointments, Chores, Classes, Exercise, Optional, Personal Projects, Research, Rest, Social, Transition. ALWAYS set this to match what the event actually is. Do not omit it: the primary calendar IS \"Transition\", so leaving this out silently files the event under Transition."}, "account": {"type": "string"}, "description": {"type": "string"}, "location": {"type": "string"}, "recurrence": {"type": "string", "description": "Only for repeating events: an RRULE, e.g. 'RRULE:FREQ=WEEKLY;BYDAY=WE' (every Wednesday) or 'RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=12'. start_time/end_time are the first occurrence."}}, "required": ["summary", "start_time", "end_time"]}},
		{"name": "create_allday_event", "description": "Create an all-day event, one day or several.", "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD, first day"}, "end_date": {"type": "string", "description": "YYYY-MM-DD, last day (inclusive). Omit for a single day."}, "calendar_name": {"type": "string"}, "account": {"type": "string"}, "description": {"type": "string"}}, "required": ["summary", "date"]}},
		{"name": "quick_add_event", "description": "Create event from natural language. Google parses the text.", "input_schema": {"type": "object", "properties": {"text": {"type": "string"}, "calendar_name": {"type": "string"}, "account": {"type": "string"}}, "required": ["text"]}},
		{"name": "delete_calendar_event", "description": "Delete an event by ID. The calendar is found automatically \u2014 you do not need calendar_id or account.", "input_schema": {"type": "object", "properties": {"event_id": {"type": "string", "description": "A real id from find_event, get_calendar_range or check_history. Never invent one."}, "calendar_id": {"type": "string", "description": "Optional hint only; omit it."}, "account": {"type": "string", "description": "Optional. Label or email; omit it."}}, "required": ["event_id"]}},
		{"name": "update_calendar_event", "description": "Update event fields (title, time, description, location). The calendar is found automatically \u2014 you do not need calendar_id or account.", "input_schema": {"type": "object", "properties": {"event_id": {"type": "string", "description": "A real id from find_event, get_calendar_range or check_history. Never invent one."}, "calendar_id": {"type": "string", "description": "Optional hint only; omit it."}, "account": {"type": "string", "description": "Optional. Label or email; omit it."}, "summary": {"type": "string"}, "start_time": {"type": "string"}, "end_time": {"type": "string"}, "description": {"type": "string"}, "location": {"type": "string"}}, "required": ["event_id"]}},
		{"name": "move_event", "description": "Move an event to a different sub-calendar. Use this whenever the user says to move, re-file, or re-categorize an existing event \u2014 do not do it by hand with delete + create. It re-creates the event on the destination calendar and removes the original, so the event gets a NEW event_id (returned as \"id\"). Works within an account and across accounts. The source calendar is found automatically.", "input_schema": {"type": "object", "properties": {"event_id": {"type": "string", "description": "A real id from find_event, get_calendar_range or check_history. Never invent one."}, "dest_calendar": {"type": "string", "description": "Target sub-calendar NAME, e.g. \"Appointments\". Call list_calendars if unsure."}, "source_calendar": {"type": "string", "description": "Optional hint only; omit it."}, "account": {"type": "string", "description": "Optional. Label or email; omit it."}}, "required": ["event_id", "dest_calendar"]}},
		{"name": "find_event", "description": "Look up events by title, so you can get a real event_id before updating, moving or deleting. Use this instead of guessing an id whenever the id is not already in front of you. Searches every calendar on every account.", "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Words from the event title, e.g. \"speed walk\". Leave empty to list everything in the window."}, "days_back": {"type": "integer", "description": "How far back to look. Default 7."}, "days_ahead": {"type": "integer", "description": "How far ahead to look. Default 30."}}}},
		{"name": "create_calendar", "description": "Create a new sub-calendar category.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "account": {"type": "string"}}, "required": ["name"]}},
		{"name": "delete_calendar", "description": "Delete a sub-calendar. Cannot delete primary.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "account": {"type": "string"}}, "required": ["name"]}},
		{"name": "list_calendars", "description": "List all sub-calendars.", "input_schema": {"type": "object", "properties": {}}},
		{"name": "get_calendar_range", "description": "Every event on every calendar and account for a range of the user's local days, with start and end times in their timezone. Use it for any schedule or free-time question — free time is whatever this doesn't show.", "input_schema": {"type": "object", "properties": {"start_date": {"type": "string", "description": "YYYY-MM-DD, local, inclusive"}, "end_date": {"type": "string", "description": "YYYY-MM-DD, local, inclusive"}}, "required": ["start_date", "end_date"]}},
	]])

# ======================== TASKS — Todoist (11 tools) ========================
# "Task list" in these tool names means a Todoist project. Priorities are the
# p1(urgent)..p4(normal) labels shown in the app, not the API's inverted 4..1.
if TODOIST_AVAILABLE:
	TOOLS.extend([_add_tool(t) for t in [
		{"name": "create_task", "description": "Create a Todoist task. These projects are an undated pool of things to pick up in spare time — tasks have NO due dates and you cannot set one. If the user wants something to happen at a specific time, that is a calendar event or a reminder, not a task.", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "task_list": {"type": "string", "description": "Todoist project name. Omit for Inbox."}, "notes": {"type": "string", "description": "Longer description body."}, "priority": {"type": "string", "enum": ["p1", "p2", "p3", "p4"], "description": "p1 = urgent, p4 = normal (default)."}, "labels": {"type": "array", "items": {"type": "string"}, "description": "Label names, without the @."}, "parent_id": {"type": "string", "description": "task_id of a parent task, to create this as a subtask."}}, "required": ["title"]}},
		{"name": "list_tasks", "description": "List active Todoist tasks. These are undated — there is no 'due today'. Scope with task_list or filter rather than pulling everything; the user has 150+ open tasks.", "input_schema": {"type": "object", "properties": {"task_list": {"type": "string", "description": "Scope to one project by name."}, "filter": {"type": "string", "description": "Todoist filter query, e.g. '#Personal Projects & p1', '@errand', 'search: kitchen'. Takes precedence over task_list."}, "label": {"type": "string", "description": "Scope to one label name."}, "max_results": {"type": "integer", "description": "Default 100. The result says so when it truncates."}}}},
		{"name": "update_task", "description": "Edit an existing Todoist task's title, notes, priority, or labels. Needs a real task_id. Due dates are not settable.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "title": {"type": "string"}, "notes": {"type": "string"}, "priority": {"type": "string", "enum": ["p1", "p2", "p3", "p4"]}, "labels": {"type": "array", "items": {"type": "string"}, "description": "Replaces the full label set."}}, "required": ["task_id"]}},
		{"name": "complete_task", "description": "Mark a Todoist task done.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
		{"name": "reopen_task", "description": "Un-complete a Todoist task that was closed by mistake.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
		{"name": "delete_task", "description": "Permanently delete a Todoist task. Destructive and not undoable — if the user just finished the task, call complete_task instead.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
		{"name": "move_task", "description": "Move a Todoist task to a different project.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "task_list": {"type": "string", "description": "Destination project name."}}, "required": ["task_id", "task_list"]}},
		{"name": "list_task_lists", "description": "List all Todoist projects. Call this if unsure what a project is named before filtering or creating into it.", "input_schema": {"type": "object", "properties": {}}},
		{"name": "create_task_list", "description": "Create a new Todoist project.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "is_favorite": {"type": "boolean"}}, "required": ["name"]}},
		{"name": "return_task_labels", "description": "List all Todoist labels (e.g. @errand). For Gmail labels use return_email_labels.", "input_schema": {"type": "object", "properties": {}}},
		{"name": "list_completed_tasks", "description": "List tasks completed in a date window (default: last 7 days, max span 3 months). Use this to see what actually got done — e.g. whether PT or a workout was logged.", "input_schema": {"type": "object", "properties": {"since": {"type": "string", "description": "YYYY-MM-DD start of window."}, "until": {"type": "string", "description": "YYYY-MM-DD end of window."}, "max_results": {"type": "integer"}}}},
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
		{"name": "return_email_labels", "description": "List all Gmail labels/folders. For Todoist labels use return_task_labels.", "input_schema": {"type": "object", "properties": {"account": {"type": "string"}}}},
		{"name": "create_email_label", "description": "Create a new Gmail label.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "account": {"type": "string"}}, "required": ["name"]}},
	]])

# ======================== DIARY & NOTES (4 tools) ========================
TOOLS.extend([_add_tool(t) for t in [
	{"name": "store_diary_entry", "description": "Store diary entry: 'bod' morning, 'eod' evening, 'note' general.", "input_schema": {"type": "object", "properties": {"entry_type": {"type": "string", "enum": ["bod", "eod", "note"]}, "content": {"type": "string"}}, "required": ["entry_type", "content"]}},
	{"name": "save_note", "description": "Save a note to local file.", "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean"}}, "required": ["filename", "content"]}},
	{"name": "read_note", "description": "Read a saved note.", "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}},
	{"name": "list_notes", "description": "List all saved notes.", "input_schema": {"type": "object", "properties": {}}},
	{"name": "remember_rule", "description": "Save a persistent rule about how YOU should behave in future conversations (not a fact about the user's life — that's save_fact). Call this whenever the user says 'remember', 'save as a rule', 'add a rule', 'always do X', 'never do Y', or similar. It's appended to notes/savvy_rules.md, which is loaded into every future conversation's context.", "input_schema": {"type": "object", "properties": {"rule": {"type": "string", "description": "The rule, in clear declarative form (e.g. 'Always fetch real event IDs from get_calendar_range before calling update_calendar_event or delete_calendar_event')"}}, "required": ["rule"]}},
]])

# ======================== TRAVEL & PLACES (5 tools) ========================
# Travel time is looked up between saved places, not raw addresses — almost no
# calendar event carries a usable address, but they all happen somewhere with a
# name the user already uses ("lab", "Marino", "home").
if TRAVEL_AVAILABLE:
	TOOLS.extend([_add_tool(t) for t in [
		{"name": "estimate_travel_time", "description": "How long it takes to get between two saved places. Use before scheduling anything back-to-back at different locations.", "input_schema": {"type": "object", "properties": {"origin": {"type": "string", "description": "Place name, key, or alias, e.g. 'home', 'lab', 'Marino'."}, "destination": {"type": "string"}, "mode": {"type": "string", "enum": ["transit", "walk", "bike", "drive"], "description": "Omit to use the default (transit). Use 'walk' for short hops on the same campus."}}, "required": ["origin", "destination"]}},
		{"name": "check_travel_gap", "description": "Check whether a gap between two events is enough to get from one place to the other, including the user's buffer. Call this whenever you schedule something that ends shortly before an event somewhere else, or move an event near one at a different location. Returns feasible true/false and the slack.", "input_schema": {"type": "object", "properties": {"origin": {"type": "string"}, "destination": {"type": "string"}, "leave_at": {"type": "string", "description": "ISO datetime the user can leave, i.e. when the first event ends."}, "arrive_by": {"type": "string", "description": "ISO datetime they must arrive, i.e. when the second event starts."}, "mode": {"type": "string", "enum": ["transit", "walk", "bike", "drive"]}}, "required": ["origin", "destination", "leave_at", "arrive_by"]}},
		{"name": "list_places", "description": "List every saved place with its aliases. Call this when unsure whether a location is known, before asking the user about it.", "input_schema": {"type": "object", "properties": {}}},
		{"name": "save_place", "description": "Save a new place so travel times work for it. Ask the user for the address when they mention somewhere new, then save it — but only somewhere they go repeatedly, not a one-off.", "input_schema": {"type": "object", "properties": {"key": {"type": "string", "description": "Short id, e.g. 'lab', 'marino'."}, "name": {"type": "string", "description": "Human name, e.g. 'Northeastern lab'."}, "address": {"type": "string", "description": "Street address, passed straight to the routing API."}, "aliases": {"type": "array", "items": {"type": "string"}, "description": "Other names the user calls it, e.g. ['school','campus']."}, "default_mode": {"type": "string", "enum": ["transit", "walk", "bike", "drive"]}}, "required": ["key", "name"]}},
		{"name": "schedule_travel_event", "description": "Block travel time on the calendar as a real event. Give it where you're coming from, where you're going, and when you need to ARRIVE — it works backwards from the arrival time. Use this whenever you schedule something at a different location from what comes before it, so the travel is actually reserved instead of implied. Refuses rather than guessing if it has no estimate for the route.", "input_schema": {"type": "object", "properties": {"origin": {"type": "string", "description": "Place name, key, or alias."}, "destination": {"type": "string"}, "arrive_by": {"type": "string", "description": "ISO datetime you need to be there, i.e. when the next event starts."}, "mode": {"type": "string", "enum": ["transit", "walk", "bike", "drive"]}, "calendar_name": {"type": "string", "description": "Sub-calendar. Omit for the default (Transition), which is where travel belongs."}, "include_buffer": {"type": "boolean", "description": "Reserve the buffer alongside travel so the block ends exactly at arrival. Default true."}}, "required": ["origin", "destination", "arrive_by"]}},
		{"name": "save_travel_time", "description": "Record a travel time the user has confirmed themselves, in minutes. Always beats a routed estimate, needs no address, and costs no API call. Use it whenever they tell you how long a trip actually takes.", "input_schema": {"type": "object", "properties": {"origin": {"type": "string"}, "destination": {"type": "string"}, "minutes": {"type": "integer"}, "mode": {"type": "string", "enum": ["transit", "walk", "bike", "drive"]}}, "required": ["origin", "destination", "minutes"]}},
	]])

# ======================== MEMORY CORRECTION (2 tools) ========================
# Extracted facts are re-injected into every prompt, so a wrong one resurfaces
# forever no matter how often the user corrects it in conversation. Until these
# existed the only remedy was /forget, which also destroys all history.
TOOLS.extend([_add_tool(t) for t in [
	{"name": "save_fact", "description": "Store something true about the user's life in long-term memory: 'my new PT is Sam', 'I did the laundry today', 'the paper deadline moved to Oct 16'. Use `replaces` to delete the outdated version in the same call. Facts are NOT rules — remember_rule is only for instructions about how you should behave.", "input_schema": {"type": "object", "properties": {"fact": {"type": "string", "description": "One complete standalone statement, e.g. 'User's physical therapist is Sam.'"}, "type": {"type": "string", "enum": ["stable", "situational", "ephemeral"], "description": "stable: true for months (people, health, goals). situational: true for now (projects, deadlines, arrangements). ephemeral: a mood or a single day."}, "replaces": {"type": "string", "description": "Optional substring of the outdated fact(s) to delete, e.g. 'last load of laundry'. search_memory_facts first if unsure."}}, "required": ["fact", "type"]}},
	{"name": "search_memory_facts", "description": "Search stored facts for a literal phrase, to see what memory actually holds about something. Use before forget_fact to check what would be removed.", "input_schema": {"type": "object", "properties": {"pattern": {"type": "string", "description": "Substring to match, case-insensitive, e.g. a project name or phrase."}}, "required": ["pattern"]}},
	{"name": "forget_fact", "description": "Permanently delete every stored fact containing a phrase. Call this whenever the user says something stored is wrong or outdated — 'forget X', 'there is no X', 'that's not true any more', 'stop mentioning X'. Saying you'll remember in the reply text does NOT remove anything; the fact stays in memory and keeps coming back. Never write a rule telling yourself not to mention something: rules are injected verbatim into every prompt, so that guarantees the opposite. Delete the facts instead.", "input_schema": {"type": "object", "properties": {"pattern": {"type": "string", "description": "Substring identifying the facts to remove. Be specific enough not to catch unrelated facts — search_memory_facts first if unsure. Do not quote the phrase back in your reply either; just confirm it's gone."}}, "required": ["pattern"]}},
]])

# ======================== GOALS (4 tools) ========================
# Explicit goals with targets, sub-goals and a progress log (goals.py). Active
# goals are already in the prompt; these tools change them.
import goals as _goals
TOOLS.extend([_add_tool(t) for t in [
	{"name": "add_goal", "description": "Add a goal the user explicitly states they want to pursue. Make the target measurable when you can (\"5 strict pull-ups\", \"in bed by midnight 5 nights/week\"). Use parent_id to make it a sub-goal. Don't add goals the user didn't ask for — suggest them instead.", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "category": {"type": "string", "description": "e.g. fitness, health, language, career, social, home, creative, sleep"}, "target": {"type": "string", "description": "What done looks like, measurably."}, "why": {"type": "string", "description": "The user's reason, if they gave one."}, "due_date": {"type": "string", "description": "YYYY-MM-DD, if there's a deadline."}, "parent_id": {"type": "integer", "description": "goal_id this is a step toward."}}, "required": ["title"]}},
	{"name": "list_goals", "description": "List goals with their targets and latest progress. Active goals are already in your context; use this for done/paused/dropped ones or full progress history.", "input_schema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["active", "paused", "done", "dropped", "all"]}, "category": {"type": "string"}}}},
	{"name": "update_goal", "description": "Change a goal: edit its target or title, or set status done/paused/dropped/active. Needs a real goal_id.", "input_schema": {"type": "object", "properties": {"goal_id": {"type": "integer"}, "title": {"type": "string"}, "category": {"type": "string"}, "target": {"type": "string"}, "why": {"type": "string"}, "due_date": {"type": "string"}, "status": {"type": "string", "enum": ["active", "paused", "done", "dropped"]}}, "required": ["goal_id"]}},
	{"name": "log_goal_progress", "description": "Record progress toward a goal when the user reports it (\"did 3 pull-ups today\", \"finished Arabic unit 4\"). value is the measurement if there is one.", "input_schema": {"type": "object", "properties": {"goal_id": {"type": "integer"}, "note": {"type": "string"}, "value": {"type": "string", "description": "e.g. '3 pull-ups', '2/3 workouts this week'"}}, "required": ["goal_id", "note"]}},
]])

# ======================== MEALS (5 tools) ========================
# Weekly meal planner (meals.py). Weekly limits are enforced in the tool: an
# over-limit add fails with an explanation the model must relay.
import meals as _meals
_MEAL_WEEK = {"type": "string", "description": "'this', 'next', '+2' (weeks from now), or any YYYY-MM-DD in that week. Omit to save as an idea in the backlog."}
TOOLS.extend([_add_tool(t) for t in [
	{"name": "add_meal", "description": "Plan a meal or drink for a week, or save a recipe idea (omit week). A meal is ONE main plus 1-2 sides; together they must cover protein and plenty of vegetables (the user loves vegetables; carbs are optional — pasta sometimes). If the main lacks protein or vegetables, add sides that fill the gap. Respect any temporary diet in context. Meals are tracked per week only — never pick a day; the user puts them on the calendar. If the week is full the call fails and explains why — tell the user and offer the week with room, the backlog, or a swap; only pass allow_over_limit after they insist.", "input_schema": {"type": "object", "properties": {"main": {"type": "string", "description": "Main dish, or the drink's name."}, "kind": {"type": "string", "enum": ["meal", "drink"]}, "sides": {"type": "array", "items": {"type": "string"}, "description": "1-2 sides (meals only)."}, "week": _MEAL_WEEK, "notes": {"type": "string"}, "allow_over_limit": {"type": "boolean"}}, "required": ["main"]}},
	{"name": "get_meal_plan", "description": "Meals and drinks planned for a week, or the recipe-idea backlog (week='idea'). This week and next are already in your context.", "input_schema": {"type": "object", "properties": {"week": {"type": "string", "description": "'this', 'next', '+2', YYYY-MM-DD, or 'idea'."}}}},
	{"name": "update_meal", "description": "Change a planned meal: swap sides, move it to another week (week='idea' sends it back to the backlog), or mark it cooked/skipped. Moving into a full week fails like add_meal does.", "input_schema": {"type": "object", "properties": {"meal_id": {"type": "integer"}, "main": {"type": "string"}, "sides": {"type": "array", "items": {"type": "string"}}, "week": {"type": "string"}, "status": {"type": "string", "enum": ["planned", "cooked", "skipped"]}, "notes": {"type": "string"}, "allow_over_limit": {"type": "boolean"}}, "required": ["meal_id"]}},
	{"name": "remove_meal", "description": "Delete a planned meal or recipe idea entirely. To keep it for later, update_meal with week='idea' instead.", "input_schema": {"type": "object", "properties": {"meal_id": {"type": "integer"}}, "required": ["meal_id"]}},
	{"name": "update_meal_settings", "description": "Change meal-planner settings: when the weekly meal reminder is sent, a temporary diet and when it ends, or the weekly limits (only if the user explicitly changes their goal).", "input_schema": {"type": "object", "properties": {"reminder_day": {"type": "string"}, "reminder_time": {"type": "string", "description": "HH:MM, 24h"}, "temporary_diet": {"type": "string", "description": "e.g. 'low-FODMAP'; '' clears it"}, "diet_until": {"type": "string", "description": "YYYY-MM-DD"}, "max_meals": {"type": "integer"}, "max_drinks": {"type": "integer"}, "max_total": {"type": "integer"}}}},
]])

# ======================== REST DAYS (2 tools) ========================
# Crash-out early warning (rest.py): explicitly logged bad days, or a packed
# stretch ahead, suggest a rest day. Detection is deterministic.
import rest as _rest
TOOLS.extend([_add_tool(t) for t in [
	{"name": "log_bad_day", "description": "Record a bad day ONLY when the user explicitly says a day was bad — 'today was awful', 'I crashed out', 'yesterday was a write-off'. Never infer one from mood, low energy mentioned in passing, or a missing diary entry. Returns whether a rest day is now worth suggesting.", "input_schema": {"type": "object", "properties": {"note": {"type": "string", "description": "What they said about the day, briefly."}, "date": {"type": "string", "description": "YYYY-MM-DD if it was a different day; omit for today."}}, "required": ["note"]}},
	{"name": "check_rest_need", "description": "Whether a rest day is due: recently logged bad days, or 3+ packed days coming up, and the best day in the next few to make a rest day.", "input_schema": {"type": "object", "properties": {}}},
]])

# ======================== HISTORY (1 tool) ========================
TOOLS.extend([_add_tool(t) for t in [
	{"name": "check_history", "description": "Search recent write actions (created/updated/deleted/moved calendar events, tasks, emails, notes) to find things like an event_id or task_id from something done earlier. ALWAYS call this before update_calendar_event, delete_calendar_event, move_event, complete_task, or delete_task if you don't already have the exact ID in the current conversation — never guess or fabricate an ID.", "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Keyword to search for, e.g. an event title, task name, or tool name. Leave empty to list all recent write actions."}, "weeks_back": {"type": "integer", "description": "How many additional past weeks to search, beyond the current one. Default 1."}}}},
]])

# ======================== REMINDERS (4 tools) ========================
TOOLS.extend([_add_tool(t) for t in [
	{"name": "schedule_reminder", "description": "Schedule a one-time check-in for a specific future date and time — separate from the fixed daily BOD/EOD/check-in schedule. At that time, the secretary will autonomously run the given instruction (checking calendar/diary/tasks as needed, and taking any real action described, e.g. creating a calendar event) and then message the user with what it found/did. Use this whenever the user asks to be checked on at a specific time, especially with conditional logic, e.g. 'check in with me at 5, and if I've done my PT, add a rest day tomorrow'.", "input_schema": {"type": "object", "properties": {"when": {"type": "string", "description": "ISO datetime for when to run this, e.g. '2026-08-20T17:00:00-04:00'. Resolve relative times ('today at 5', 'tomorrow morning') to a real ISO datetime yourself using the current date/time you already have."}, "instruction": {"type": "string", "description": "What to check and/or do at that time, in clear imperative form, including any conditions (e.g. 'Check if a PT session was logged in today's diary. If so, create a Rest day all-day event tomorrow on the Rest calendar. If not, just remind me to do PT.')"}}, "required": ["when", "instruction"]}},
	{"name": "list_reminders", "description": "List all pending one-off scheduled reminders (from schedule_reminder) that haven't fired yet, soonest first.", "input_schema": {"type": "object", "properties": {}}},
	{"name": "cancel_reminder", "description": "Delete a pending scheduled reminder so it never fires — when the user no longer needs it, already did the thing, or asks to remove it. Get the id from list_reminders or the schedule_reminder result; never guess one.", "input_schema": {"type": "object", "properties": {"reminder_id": {"type": "string", "description": "The 8-character id shown in brackets by list_reminders."}}, "required": ["reminder_id"]}},
	{"name": "update_reminder", "description": "Change a pending reminder's time and/or instruction, instead of cancelling and re-creating it. Get the id from list_reminders; never guess one.", "input_schema": {"type": "object", "properties": {"reminder_id": {"type": "string"}, "when": {"type": "string", "description": "New ISO datetime, e.g. '2026-10-07T16:00:00-04:00'. Omit to keep the time."}, "instruction": {"type": "string", "description": "New instruction. Omit to keep it."}}, "required": ["reminder_id"]}},
]])


# =====================================================================
# EXECUTION
# =====================================================================
def _execute_tool_inner(name: str, inp: dict) -> str:
	"""Pure dispatch — _importance and other meta fields already stripped."""
	# ---- CALENDAR ----
	if name == "create_calendar_event":
		r = create_event(summary=inp["summary"], start_time=inp["start_time"], end_time=inp["end_time"], calendar_name=inp.get("calendar_name"), account=inp.get("account"), description=inp.get("description", ""), location=inp.get("location", ""), recurrence=inp.get("recurrence"))
		return _dumps({"success": bool(r), "event": r}) if r else _dumps({"success": False, "error": "Failed"})

	elif name == "create_allday_event":
		r = create_allday_event(summary=inp["summary"], date=inp["date"], calendar_name=inp.get("calendar_name"), account=inp.get("account"), description=inp.get("description", ""), end_date=inp.get("end_date"))
		return _dumps({"success": bool(r), "event": r}) if r else _dumps({"success": False})

	elif name == "quick_add_event":
		r = quick_add_event(text=inp["text"], calendar_name=inp.get("calendar_name"), account=inp.get("account"))
		return _dumps({"success": bool(r), "event": r}) if r else _dumps({"success": False})

	elif name == "delete_calendar_event":
		return _dumps(delete_event(
			inp["event_id"], inp.get("calendar_id"), inp.get("account")))

	elif name == "update_calendar_event":
		hit = resolve_event(inp["event_id"], inp.get("account"),
		                    calendar_hint=inp.get("calendar_id"))
		if not hit:
			return _dumps({"success": False, "error":
				f"No event with id {inp['event_id']!r} exists on any calendar. "
				"Fetch a current event_id with find_event or get_calendar_range — "
				"do not guess one."})
		event = hit["event"]
		for f in ["summary", "description", "location"]:
			if f in inp: event[f] = inp[f]
		if "start_time" in inp: event["start"] = {"dateTime": inp["start_time"], "timeZone": user_tz().key}
		if "end_time" in inp: event["end"] = {"dateTime": inp["end_time"], "timeZone": user_tz().key}
		try:
			r = hit["service"].events().update(
				calendarId=hit["calendar_id"], eventId=inp["event_id"], body=event,
			).execute()
		except Exception as e:
			return _dumps({"success": False, "error": str(e)})
		return _dumps({"success": True, "summary": r.get("summary"),
		               "calendar": hit["calendar"], "account": hit["label"]})

	elif name == "move_event":
		return _dumps(move_event(
			inp["event_id"], inp.get("source_calendar"), inp["dest_calendar"],
			inp.get("account")))

	elif name == "create_calendar":
		target = resolve_write_account(inp.get("account"))
		for label, service in _get_services():
			if target and label != target:
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
		return _dumps({"calendars": [{"name": c["name"], "account": c["account"], "primary": c["primary"], "color": c.get("color", "")} for c in cals]})

	elif name == "get_calendar_range":
		events = get_range_events(inp["start_date"], inp["end_date"])
		return format_events_for_context(events) if events else f"No events {inp['start_date']} to {inp['end_date']}"

	elif name == "find_event":
		# Exists so the model has a cheap way to turn "the speed walk event" into a
		# real event_id. Without it, it fabricated ids like "the-speed-walk-event-id"
		# and every following update/delete 404'd.
		q = (inp.get("query") or "").strip().lower()
		now = datetime.now(timezone.utc)
		start = now - timedelta(days=inp.get("days_back", 7))
		end = now + timedelta(days=inp.get("days_ahead", 30))
		events = _fetch_events(start, end, max_results=250)
		if q:
			events = [e for e in events if q in (e.get("summary") or "").lower()]
		if not events:
			return f"No events matching {inp.get('query', '')!r} in that window."
		return format_events_for_context(events[:40])

	# ---- TASKS (Todoist) ----
	elif name == "create_task":
		r = create_task(title=inp["title"], task_list=inp.get("task_list"), notes=inp.get("notes", ""), priority=inp.get("priority"), labels=inp.get("labels"), parent_id=inp.get("parent_id"))
		return _dumps({"success": True, "task": format_tasks_for_context([r])})

	elif name == "list_tasks":
		# Fetch one past the limit so truncation can be detected and declared —
		# a silently short list makes the model confidently wrong about what's left.
		limit = int(inp.get("max_results", 100))
		rows = list_tasks(task_list=inp.get("task_list"), filter_query=inp.get("filter"), label=inp.get("label"), max_results=limit + 1)
		truncated = len(rows) > limit
		out = format_tasks_for_context(rows[:limit])
		if truncated:
			out += f"\n\n(truncated at {limit}; more tasks exist — narrow with `filter` or `task_list`, or raise max_results)"
		return out

	elif name == "update_task":
		r = update_task(task_id=inp["task_id"], title=inp.get("title"), notes=inp.get("notes"), priority=inp.get("priority"), labels=inp.get("labels"))
		return _dumps({"success": True, "task": format_tasks_for_context([r])})

	elif name in ("complete_task", "reopen_task", "delete_task"):
		# Read the title first so the result can say which task, not just an id.
		try:
			title = (get_task(inp["task_id"]) or {}).get("content", "")
		except Exception:
			title = ""
		fn = {"complete_task": complete_task, "reopen_task": reopen_task, "delete_task": delete_task}[name]
		return _dumps({"success": fn(inp["task_id"]), "task": title})

	elif name == "move_task":
		r = move_task(inp["task_id"], inp["task_list"])
		return _dumps({"success": True, "task": format_tasks_for_context([r])})

	elif name == "list_task_lists":
		return format_projects_for_context(list_projects())

	elif name == "create_task_list":
		r = create_project(inp["name"], is_favorite=inp.get("is_favorite", False))
		return _dumps({"success": True, "project": {"id": r.get("id"), "name": r.get("name")}})

	elif name == "return_task_labels":
		labels = list_todoist_labels()
		return _dumps({"labels": [l.get("name") for l in labels]}) if labels else "(no labels)"

	elif name == "list_completed_tasks":
		done = get_completed_tasks(since=inp.get("since"), until=inp.get("until"), max_results=inp.get("max_results", 100))
		return format_tasks_for_context(done)

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

	elif name == "return_email_labels":
		labels = list_gmail_labels(inp.get("account"))
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

	elif name == "remember_rule":
		return _dumps(add_savvy_rule(inp["rule"]))

	# ---- TRAVEL & PLACES ----
	elif name == "estimate_travel_time":
		r = _travel.estimate(inp["origin"], inp["destination"], inp.get("mode"))
		if not r["ok"]:
			return _dumps({"success": False, "reason": r["note"]})
		return _dumps({"success": True, "minutes": r["minutes"], "from": r["origin"],
					   "to": r["destination"], "mode": r["mode"], "source": r["source"]})

	elif name == "check_travel_gap":
		try:
			leave = datetime.fromisoformat(inp["leave_at"])
			arrive = datetime.fromisoformat(inp["arrive_by"])
		except ValueError as e:
			return _dumps({"success": False, "error": f"Bad datetime: {e}"})
		gap = (arrive - leave).total_seconds()
		if gap < 0:
			return _dumps({"success": False, "error": "arrive_by is before leave_at."})
		r = _travel.check_gap(inp["origin"], inp["destination"], gap, inp.get("mode"), when=leave)
		if r["feasible"] is None:
			return _dumps({"success": False, "reason": r["note"], "gap_minutes": r["gap_minutes"]})
		return _dumps({"success": True, "feasible": r["feasible"], "from": r["origin"],
					   "to": r["destination"], "mode": r["mode"], "travel_minutes": r["minutes"],
					   "gap_minutes": r["gap_minutes"], "buffer_minutes": r["buffer_minutes"],
					   "slack_minutes": r["slack_minutes"], "source": r["source"]})

	elif name == "list_places":
		return _places.format_places_for_context()

	elif name == "save_place":
		address = inp.get("address", "")
		entry = _places.add(key=inp["key"], name=inp["name"], address=address,
							aliases=inp.get("aliases"),
							default_mode=inp.get("default_mode", _places.DEFAULT_MODE))
		note = "" if address else "No address given — only confirmed times will work for this place."
		return _dumps({"success": True, "place": entry.get("name"),
					   "routable": bool(address), "note": note})

	elif name == "schedule_travel_event":
		try:
			arrive = datetime.fromisoformat(inp["arrive_by"])
		except ValueError as e:
			return _dumps({"success": False, "error": f"Bad arrive_by datetime: {e}"})

		est = _travel.estimate(inp["origin"], inp["destination"], inp.get("mode"), when=arrive)
		if not est["ok"]:
			# Never invent a duration — a wrong travel block is worse than none.
			return _dumps({"success": False, "reason": est["note"]})

		seconds = est["seconds"]
		if inp.get("include_buffer", True):
			seconds += _travel.buffer_seconds()
		if seconds <= 0:
			return _dumps({"success": False, "reason": "Same place — no travel to schedule."})

		depart = arrive - timedelta(seconds=seconds)
		dest = _places.find(inp["destination"]) or {}
		mode_label = {"transit": "T", "walk": "Walk", "bike": "Bike", "drive": "Drive"}.get(est["mode"], est["mode"])
		desc = (f"{mode_label} · {est['minutes']} min travel"
				+ (f" + {_travel.buffer_seconds() // 60} min buffer" if inp.get("include_buffer", True) else "")
				+ f" · estimate source: {est['source']}")

		ev = create_event(
			summary=f"Travel: {est['origin']} → {est['destination']}",
			start_time=depart.isoformat(), end_time=arrive.isoformat(),
			calendar_name=inp.get("calendar_name"),
			description=desc, location=dest.get("address", ""),
		)
		if not ev:
			return _dumps({"success": False, "error": "Calendar rejected the travel event."})
		return _dumps({"success": True, "summary": ev.get("summary"),
					   "leave_at": depart.strftime("%-I:%M %p"),
					   "arrive_by": arrive.strftime("%-I:%M %p"),
					   "minutes_blocked": seconds // 60,
					   "travel_minutes": est["minutes"], "mode": est["mode"],
					   "source": est["source"], "calendar": ev.get("calendar_id")})

	elif name == "save_travel_time":
		mode = inp.get("mode") or _travel.default_mode()
		a, b = _places.find(inp["origin"]), _places.find(inp["destination"])
		if not a or not b:
			missing = [n for n, p in ((inp["origin"], a), (inp["destination"], b)) if not p]
			return _dumps({"success": False, "error": f"Unknown place(s): {', '.join(missing)}"})
		_places.set_override(a["key"], b["key"], mode, int(inp["minutes"]) * 60)
		return _dumps({"success": True, "from": a["name"], "to": b["name"],
					   "minutes": inp["minutes"], "mode": mode})

	# ---- MEMORY CORRECTION ----
	elif name == "save_fact":
		from memory import Memory
		mem = Memory(session_id="fact_save")
		try:
			removed = mem.forget_facts(inp["replaces"]) if inp.get("replaces") else []
			added = mem.store_fact(inp["fact"], inp.get("type", "situational"))
		finally:
			mem.close()
		from memory import normalize_fact_type, fact_lifetime
		fact_type = normalize_fact_type(inp.get("type", "situational"))
		return _dumps({"success": True, "stored": inp["fact"], "new": added,
					   "type": fact_type, "lifetime": fact_lifetime(fact_type),
					   "replaced": removed[:10]})

	elif name == "search_memory_facts":
		from memory import Memory
		mem = Memory(session_id="fact_search")
		try:
			hits = mem.find_facts(inp["pattern"])
		finally:
			mem.close()
		if not hits:
			return f"(no stored facts match {inp['pattern']!r})"
		lines = [f"- {h['fact']}" for h in hits[:40]]
		out = f"{len(hits)} stored fact(s) matching {inp['pattern']!r}:\n" + "\n".join(lines)
		if len(hits) > 40:
			out += f"\n… and {len(hits) - 40} more"
		return out

	elif name == "forget_fact":
		from memory import Memory
		mem = Memory(session_id="fact_forget")
		try:
			removed = mem.forget_facts(inp["pattern"])
		finally:
			mem.close()
		# The deleted text is returned so the audit log keeps a recoverable record.
		return _dumps({"success": True, "deleted_count": len(removed),
					   "deleted": removed[:40], "pattern": inp["pattern"]})

	# ---- GOALS ----
	elif name == "add_goal":
		return _dumps({"success": True, "goal": _goals.add_goal(
			inp["title"], inp.get("category", ""), inp.get("target", ""), inp.get("why", ""),
			inp.get("due_date"), inp.get("parent_id"))})

	elif name == "list_goals":
		return _goals.format_goals_for_context(
			_goals.list_goals(inp.get("status", "active"), inp.get("category"), with_progress=10))

	elif name == "update_goal":
		fields = {k: inp.get(k) for k in ("title", "category", "target", "why", "due_date", "status")}
		return _dumps({"success": True, "goal": _goals.update_goal(int(inp["goal_id"]), **fields)})

	elif name == "log_goal_progress":
		return _dumps({"success": True, **_goals.log_progress(int(inp["goal_id"]), inp["note"], inp.get("value", ""))})

	# ---- MEALS ----
	elif name == "add_meal":
		return _dumps(_meals.add_meal(
			inp["main"], inp.get("kind", "meal"), inp.get("sides"), inp.get("week"), None,
			inp.get("notes", ""), bool(inp.get("allow_over_limit"))))

	elif name == "get_meal_plan":
		return _meals.format_week(inp.get("week", "this"))

	elif name == "update_meal":
		return _dumps(_meals.update_meal(
			int(inp["meal_id"]), inp.get("main"), inp.get("sides"), inp.get("week"), None,
			inp.get("status"), inp.get("notes"), bool(inp.get("allow_over_limit"))))

	elif name == "remove_meal":
		return _dumps(_meals.remove_meal(int(inp["meal_id"])))

	elif name == "update_meal_settings":
		keys = ("reminder_day", "reminder_time", "temporary_diet", "diet_until", "max_meals", "max_drinks", "max_total")
		return _dumps({"success": True, "settings": _meals.update_settings(**{k: inp.get(k) for k in keys})})

	# ---- REST DAYS ----
	elif name == "log_bad_day":
		store_entry("bad_day", inp["note"], date=inp.get("date"))
		return _dumps({"success": True, "logged": inp.get("date") or "today",
					   "rest_check": _rest.format_assessment(_rest.assess())})

	elif name == "check_rest_need":
		return _rest.format_assessment(_rest.assess())

	# ---- HISTORY ----
	elif name == "check_history":
		return _check_history_impl(query=inp.get("query", ""), weeks_back=inp.get("weeks_back", 1))

	# ---- REMINDERS ----
	elif name == "schedule_reminder":
		r = add_reminder(inp["when"], inp["instruction"])
		return _dumps({"success": True, "reminder": r})

	elif name in ("cancel_reminder", "update_reminder"):
		if inp.get("when"):
			try:
				datetime.fromisoformat(inp["when"])
			except ValueError as e:
				return _dumps({"success": False, "error": f"Bad datetime: {e}"})
		rid = str(inp["reminder_id"]).strip("[] ")
		r = (remove_reminder(rid) if name == "cancel_reminder"
		     else update_reminder(rid, inp.get("when"), inp.get("instruction")))
		if not r:
			return _dumps({"success": False, "error":
				f"No pending reminder with id {rid!r} — it may have already fired. "
				"Call list_reminders for the real ids."})
		return _dumps({"success": True, "reminder": r})

	elif name == "list_reminders":
		pending = _list_reminders_impl()
		if not pending:
			return "(no pending reminders)"
		return "\n".join(f"- [{r['id']}] {r['due_at']}: {r['instruction']}" for r in pending)

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
		if get_danger_level(name) >= 1:
			log_task(name, clean_inp, result)
		return result
	except Exception as e:
		log_tool_error(call_id, name, e)
		error_result = _dumps({"error": str(e)})
		if get_danger_level(name) >= 1:
			log_task(name, clean_inp, error_result)
		return error_result


# =====================================================================
# TOOL-USE CONVERSATION LOOP
# =====================================================================
# How many times a turn may be resumed after hitting max_tokens before we give
# up. Each continuation is a fresh API call, so this bounds cost and latency.
MAX_CONTINUATIONS = 6


def _cache_marker(system) -> dict:
	"""Same TTL as the marker build_context put on the system prompt, if any."""
	if isinstance(system, list):
		for block in system:
			if isinstance(block, dict) and block.get("cache_control"):
				return block["cache_control"]
	return {"type": "ephemeral"}


# Models that take a server-side refusal fallback ("default" routes by refusal
# category, so there's no fallback model list to maintain).
_FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}


def request_options(config: dict, model: str, max_tokens: int) -> tuple[dict, int]:
	"""Extra messages.create kwargs from config, and max_tokens adjusted for thinking.

	Config keys (all optional — omitted means the model's defaults):
	  effort    "low" | "medium" | "high" | "xhigh" | "max"
	  thinking  "adaptive" | "between_tools" (Sonnet 5.5 only) | "off"
	Thinking counts against max_tokens, so with thinking on the budget is
	raised to at least `thinking_max_tokens` (default 8000) — a 1024 cap cut
	replies off mid-sentence.
	"""
	opts: dict = {}
	if config.get("effort"):
		opts["output_config"] = {"effort": config["effort"]}
	thinking = config.get("thinking")
	if thinking and thinking != "off":
		opts["thinking"] = {"type": thinking}
	thinks = (thinking not in (None, "off", "between_tools")) or (thinking is None and model in _FALLBACK_MODELS)
	if thinks:
		max_tokens = max(max_tokens, int(config.get("thinking_max_tokens", 8000)))
	if model in _FALLBACK_MODELS and config.get("refusal_fallback", True):
		opts["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
		opts["extra_body"] = {"fallbacks": "default"}
	return opts, max_tokens


def read_only_tools() -> list[dict]:
	"""Tools that only read (danger level 0) — for runs nobody is watching, like check-ins."""
	return [t for t in TOOLS if get_danger_level(t["name"]) == 0]


def chat_with_tools(client, model, system, messages, max_tokens=1024, stream_callback=None, tool_event_callback=None, max_continuations=MAX_CONTINUATIONS, tools=None, options=None):
	"""Run the tool-use loop to completion, resuming across token limits.

	Two things the naive loop got wrong:

	1. Hitting the cap set stop_reason to "max_tokens" rather than "tool_use",
	   so any tool calls the model had already emitted were dropped on the floor
	   and never executed. Asking for several events at once silently did
	   nothing. Pending tool calls now run regardless of why the turn ended.
	2. A long final answer was truncated and the user told to ask again. It now
	   resumes on its own and the pieces are joined.
	"""
	current_messages = list(messages)
	tools = TOOLS if tools is None else tools
	allowed = {t["name"] for t in tools}
	total_in = total_out = 0
	text_accum: list[str] = []
	continuations = 0

	while True:
		# Top-level cache_control caches the growing conversation tail, so each
		# tool round re-reads the previous round's prefix instead of paying for
		# it again. The fixed tools+instructions prefix has its own marker on the
		# first system block (core.build_context); the TTLs must match.
		resp = client.messages.create(model=model, max_tokens=max_tokens, system=system, messages=current_messages, tools=tools or None, cache_control=_cache_marker(system), **(options or {}))
		total_in += resp.usage.input_tokens
		total_out += resp.usage.output_tokens
		# One line per request, so cache behavior is visible in the service logs.
		u = resp.usage
		print(f"[usage] {model}: in {u.input_tokens:,} · cache read {getattr(u, 'cache_read_input_tokens', 0) or 0:,} · "
		      f"cache write {getattr(u, 'cache_creation_input_tokens', 0) or 0:,} · out {u.output_tokens:,} · {resp.stop_reason}")

		text_parts, tool_uses = [], []
		for block in resp.content:
			if block.type == "text":
				text_parts.append(block.text)
				if stream_callback:
					stream_callback(block.text)
			elif block.type == "tool_use":
				tool_uses.append(block)

		current_messages.append({"role": "assistant", "content": resp.content})

		# Execute whatever the model asked for, even if the turn was cut short
		# mid-thought — those calls are work it already decided on.
		if tool_uses:
			if resp.stop_reason == "max_tokens":
				print(f"[tools] Hit max_tokens ({max_tokens}) mid-turn with {len(tool_uses)} pending call(s) — running them and continuing.")
			# Narration that precedes tool calls isn't the final answer.
			text_accum = []
			if stream_callback and text_parts:
				stream_callback("\n")  # keep the tool lines off the narration's last line
			tool_results = []
			for tu in tool_uses:
				try:
					if tu.name not in allowed:
						result = _dumps({"success": False, "error": f"{tu.name} is not available in this run."})
					else:
						result = execute_tool(tu.name, tu.input)
				except Exception as e:
					# A tool_use truncated by the cap can carry incomplete input.
					# Report it back so the model can reissue it properly.
					result = _dumps({"error": f"Malformed or failed tool call: {e}"})
				if tool_event_callback:
					tool_event_callback(tu.name, tu.input, result)
				tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result})
			current_messages.append({"role": "user", "content": tool_results})
			continue

		text_accum.extend(text_parts)

		if resp.stop_reason == "refusal":
			reply = "".join(text_accum).strip() or "Sorry — I can't help with that one."
			return reply, total_in, total_out

		if resp.stop_reason == "max_tokens" and continuations < max_continuations:
			continuations += 1
			print(f"[tools] Reply hit max_tokens — resuming ({continuations}/{max_continuations}).")
			current_messages.append({
				"role": "user",
				"content": "Continue from exactly where you stopped. Do not repeat anything you already wrote, and do not restart the reply.",
			})
			continue

		reply = "".join(text_accum)
		if resp.stop_reason == "max_tokens":
			print(f"[tools] Still truncated after {continuations} continuation(s); returning what we have.")
			reply += "\n\n[cut off — ask me to continue]"
		return reply, total_in, total_out
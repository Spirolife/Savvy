"""
Google Calendar integration — multi-account, multi-calendar.
Reads from ALL sub-calendars (appointments, chores, etc.) and can create events.

Datetime convention:
    All event start/end values stored on returned dicts are timezone-aware
    `datetime` objects (UTC for all-day events, original offset preserved for
    timed events). All-day events are flagged via `is_allday=True`.
    Callers should not call `fromisoformat` on these values themselves.

Timezones: everything sent to and stored by Google is an absolute instant, and
API windows are passed in UTC. But "today", "this week" and a date range are
the USER's days, so their boundaries are computed at local midnight (config
`timezone`, default America/New_York) and only then converted to UTC. Using
UTC midnight instead shifted every window by 4-5 hours: late-evening events
fell off "today" and the previous evening's leaked in. Anything shown to the
model is rendered in the user's timezone.
"""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build

from google_auth import get_all_credentials, get_account_labels, stored_account_email

try:
    from paths import CONFIG_PATH
except ImportError:
    from pathlib import Path as _Path
    CONFIG_PATH = _Path(__file__).parent.parent / "credentials" / "config.json"


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


DEFAULT_TIMEZONE = "America/New_York"


def user_tz() -> ZoneInfo:
    """The user's timezone, from config `timezone`."""
    name = (_load_config().get("timezone") or DEFAULT_TIMEZONE).strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def local_day_start(day) -> datetime:
    """Local midnight at the start of `day` (a date, or a datetime in any zone)."""
    if isinstance(day, datetime):
        day = day.astimezone(user_tz()).date()
    return datetime(day.year, day.month, day.day, tzinfo=user_tz())


def local_range(start_date: str, end_date: str) -> tuple[datetime, datetime]:
    """UTC window covering local days start_date..end_date (YYYY-MM-DD), inclusive."""
    start = local_day_start(datetime.strptime(start_date, "%Y-%m-%d").date())
    end = local_day_start(datetime.strptime(end_date, "%Y-%m-%d").date() + timedelta(days=1))
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def resolve_write_account(account: str | None) -> str | None:
    """Decide which account a newly-created event belongs to.

    Config keys:
        calendar_account_lock    — if set, ALL event creation goes here and any
                                   other account passed in is overridden. For
                                   someone who only ever uses one account.
        default_calendar_account — without a lock: used when the caller names
                                   no account. For someone who uses several.

    The lock exists because the account label is chosen by the model on every
    call. A prose rule in savvy_rules.md is a suggestion it can misread — and
    did, for weeks, off a stale rule written while the labels were swapped.
    This makes the destination a config fact rather than a per-call guess.
    """
    config = _load_config()
    lock = (config.get("calendar_account_lock") or "").strip()
    if lock:
        if account and normalize_account(account) != normalize_account(lock):
            print(f"[calendar] account={account!r} overridden by calendar_account_lock={lock!r}")
        return normalize_account(lock) or lock
    if account:
        return normalize_account(account) or account
    default = (config.get("default_calendar_account") or "").strip()
    return normalize_account(default) or default or None


def _get_services() -> list[tuple[str, object]]:
    services = []
    for label, creds in get_all_credentials().items():
        try:
            svc = build("calendar", "v3", credentials=creds)
            services.append((label, svc))
        except Exception:
            pass
    return services


def normalize_account(account: str | None) -> str | None:
    """Map whatever the model passed to a real account label.

    The model routinely passes an email address ("aspiro.northeastern@gmail.com")
    where a label ("northeastern") is expected, because that is how the account is
    named in prose everywhere else. Every caller used to compare `label == account`
    exactly, so an email address matched no account, filtered the service list to
    empty, and the tool returned a bare failure. 17 create_calendar_event failures
    in the audit log were nothing but this.

    Returns the matching label, or None when nothing matches — None means "do not
    filter", which tries every account rather than guaranteeing failure.
    """
    if not account:
        return None
    a = account.strip().lower()
    if not a:
        return None
    labels = get_account_labels()
    for label in labels:                       # exact label
        if label.lower() == a:
            return label
    for label in labels:                       # exact email
        if stored_account_email(label).lower() == a:
            return label
    local = a.split("@")[0]
    for label in labels:                       # local-part of the email
        if stored_account_email(label).lower().split("@")[0] == local:
            return label
    for label in labels:                       # substring either direction
        if a in label.lower() or label.lower() in a:
            return label
    return None


def _services_for(account: str | None) -> list[tuple[str, object]]:
    """Services for one account, or all of them when the account is unknown."""
    label = normalize_account(account)
    services = _get_services()
    if not label:
        return services
    return [(l, s) for l, s in services if l == label] or services


def resolve_event(event_id: str, account: str | None = None,
                  calendar_hint: str | None = None) -> dict | None:
    """Find which account and calendar an event actually lives on.

    update/delete/move all defaulted `calendar_id` to "primary" and the model
    almost never passed anything else, so every event sitting in a sub-calendar
    (Exercise, Transition, Appointments — i.e. nearly all of them) 404'd. Looking
    the event up removes the need for the model to carry a correlated
    (event_id, calendar_id) pair at all.

    Returns {"label", "service", "calendar_id", "calendar", "event"} or None.
    """
    if not event_id:
        return None
    for label, service in _services_for(account):
        tried: set[str] = set()

        def _try(cal_id: str, cal_name: str = "") -> dict | None:
            if not cal_id or cal_id in tried:
                return None
            tried.add(cal_id)
            try:
                ev = service.events().get(calendarId=cal_id, eventId=event_id).execute()
            except Exception:
                return None
            if ev.get("status") == "cancelled":
                return None
            return {"label": label, "service": service, "calendar_id": cal_id,
                    "calendar": cal_name or cal_id, "event": ev}

        if calendar_hint:                       # cheap path: caller told us where
            found = find_calendar_id(calendar_hint, label)
            if found:
                hit = _try(found[0], calendar_hint)
                if hit:
                    return hit
        try:                                    # every calendar on this account
            cals = service.calendarList().list().execute().get("items", [])
        except Exception:
            cals = []
        # "primary" is an alias for a calendar that has a real name — on this
        # account it is "Transition" — so name it properly rather than reporting
        # the alias back to the model.
        primary_name = next((c.get("summary", "") for c in cals if c.get("primary")), "")
        hit = _try("primary", primary_name or "primary")
        if hit:
            return hit
        for cal in cals:
            hit = _try(cal["id"], cal.get("summary", ""))
            if hit:
                return hit
    return None


# ---------------------------------------------------------------------------
# Datetime helpers — single source of truth for parsing Google's payload
# ---------------------------------------------------------------------------
def _parse_event_time(time_node: dict) -> tuple[datetime, bool]:
    """Parse a Google event start/end node into (aware datetime, is_allday).

    Google returns one of:
        {"dateTime": "2026-05-04T14:00:00-04:00", "timeZone": "..."}  # timed
        {"date":     "2026-05-04"}                                    # all-day

    Timed events arrive with an offset and parse as aware. All-day events
    parse as naive dates; we promote them to aware UTC at midnight so they
    can be sorted and compared alongside timed events.
    """
    if "dateTime" in time_node:
        dt = datetime.fromisoformat(time_node["dateTime"])
        if dt.tzinfo is None:
            # Defensive: shouldn't happen for dateTime, but normalize anyway
            dt = dt.replace(tzinfo=timezone.utc)
        return dt, False
    # All-day: just a date string
    d = datetime.fromisoformat(time_node["date"])
    return d.replace(tzinfo=timezone.utc), True


# ---------------------------------------------------------------------------
# Calendar listing
# ---------------------------------------------------------------------------
def list_calendars() -> list[dict]:
    """List all calendars across all accounts."""
    all_calendars = []
    for label, service in _get_services():
        try:
            result = service.calendarList().list().execute()
            for cal in result.get("items", []):
                all_calendars.append({
                    "id": cal["id"],
                    "name": cal.get("summary", "(unnamed)"),
                    "account": label,
                    "primary": cal.get("primary", False),
                    "access_role": cal.get("accessRole", ""),
                    "color": cal.get("backgroundColor", ""),
                    "color_id": cal.get("colorId", ""),
                })
        except Exception as e:
            print(f"[calendar] Error listing calendars for {label}: {e}")
    return all_calendars


class CalendarError(RuntimeError):
    """A calendar write failed; the message is shown to the model as the tool result."""


def _name_key(name: str) -> str:
    """Case-, space- and plural-insensitive form: "appointment " == "Appointments"."""
    key = " ".join((name or "").lower().split())
    return key[:-1] if key.endswith("s") else key


def find_calendar_id(name_query: str, account: str | None = None) -> tuple[str, str, object] | None:
    """Find a calendar by name. Returns (calendar_id, account_label, service).

    Exact match only, ignoring case and a trailing "s". This used to be a
    substring match, so "Personal" silently resolved to "Personal Projects"
    and events landed on the wrong calendar with a success result.
    """
    query = _name_key(name_query)
    for label, service in _services_for(account):
        try:
            result = service.calendarList().list().execute()
            for cal in result.get("items", []):
                if _name_key(cal.get("summary", "")) == query:
                    return (cal["id"], label, service)
        except Exception:
            pass
    return None


def _calendar_names(account: str | None) -> list[str]:
    names = []
    for _, service in _services_for(account):
        try:
            names += [c.get("summary", "") for c in service.calendarList().list().execute().get("items", [])]
        except Exception:
            pass
    return names


def _write_target(calendar_name: str | None, account: str | None) -> tuple[str, str, object, str]:
    """(calendar_id, account_label, service, calendar_label) for a new event.

    A named calendar that doesn't exist is an error listing the real names — not
    a silent fallback to the primary calendar, which filed events under
    Transition and reported success.
    """
    account = resolve_write_account(account)
    if calendar_name:
        found = find_calendar_id(calendar_name, account)
        if not found:
            raise CalendarError(f"No calendar named {calendar_name!r}. Calendars: "
                                f"{', '.join(_calendar_names(account)) or '(none)'}.")
        cal_id, label, service = found
        return cal_id, label, service, calendar_name
    services = _services_for(account)
    if not services:
        raise CalendarError("No Google account is connected for calendar writes.")
    label, service = services[0]
    try:
        cals = service.calendarList().list().execute().get("items", [])
        primary = next((c.get("summary", "") for c in cals if c.get("primary")), "primary")
    except Exception:
        primary = "primary"
    # "primary" is the calendar named Transition on this account; say so.
    return "primary", label, service, primary


# ---------------------------------------------------------------------------
# Event fetching — reads ALL calendars per account
# ---------------------------------------------------------------------------
def _fetch_events(
    time_min: datetime, time_max: datetime, max_results: int = 50
) -> list[dict]:
    """Fetch events across all accounts and calendars in a window.

    Returned dicts have:
        start, end:  aware `datetime` (UTC for all-day events)
        is_allday:   bool
        plus the usual summary/location/description/account/calendar/calendar_id/id
    """
    all_events = []
    for label, service in _get_services():
        try:
            cal_list = service.calendarList().list().execute()
            for cal in cal_list.get("items", []):
                cal_id = cal["id"]
                cal_name = cal.get("summary", "(unnamed)")
                try:
                    result = service.events().list(
                        calendarId=cal_id,
                        timeMin=time_min.isoformat(),
                        timeMax=time_max.isoformat(),
                        maxResults=max_results,
                        singleEvents=True,
                        orderBy="startTime",
                    ).execute()
                    for event in result.get("items", []):
                        try:
                            start_dt, is_allday = _parse_event_time(event["start"])
                            end_dt, _ = _parse_event_time(event["end"])
                        except (KeyError, ValueError, TypeError) as e:
                            print(f"[calendar] Skipping event with bad time: {e}")
                            continue
                        all_events.append({
                            "summary": event.get("summary", "(no title)"),
                            "start": start_dt,
                            "end": end_dt,
                            "is_allday": is_allday,
                            "location": event.get("location", ""),
                            "description": event.get("description", "")[:200],
                            "account": label,
                            "calendar": cal_name,
                            "calendar_id": cal_id,
                            "id": event.get("id"),
                        })
                except Exception:
                    pass
        except Exception as e:
            print(f"[calendar] Error fetching from {label}: {e}")

    all_events.sort(key=lambda e: e["start"])
    return all_events


# ---------------------------------------------------------------------------
# Event creation
# ---------------------------------------------------------------------------
def create_event(
    summary: str,
    start_time: str,
    end_time: str,
    calendar_name: str | None = None,
    account: str | None = None,
    description: str = "",
    location: str = "",
    recurrence: list[str] | str | None = None,
) -> dict | None:
    """Create a calendar event.

    Args:
        summary: Event title
        start_time: ISO format datetime (e.g. "2026-03-25T14:00:00-04:00")
        end_time: ISO format datetime
        calendar_name: Sub-calendar name (e.g. "appointments", "chores").
                       Uses primary if not specified.
        account: Account label ("personal", "northeastern"). Overridden by
                 calendar_account_lock; falls back to default_calendar_account.
        description: Optional event description
        location: Optional location
        recurrence: RRULE line(s), e.g. "RRULE:FREQ=WEEKLY;BYDAY=WE;COUNT=10".
    """
    target_cal_id, target_account, target_service, landed_on = _write_target(calendar_name, account)

    # timeZone alongside the offset: the offset fixes the instant, the zone is
    # what Google expands a recurrence in (it rejects recurring events without
    # one) so a weekly 7pm class stays 7pm across the DST change.
    tz_name = user_tz().key
    event_body = {
        "summary": summary,
        "start": {"dateTime": start_time, "timeZone": tz_name},
        "end": {"dateTime": end_time, "timeZone": tz_name},
    }
    if description:
        event_body["description"] = description
    if location:
        event_body["location"] = location
    if recurrence:
        rules = [recurrence] if isinstance(recurrence, str) else list(recurrence)
        event_body["recurrence"] = [r if r.startswith(("RRULE:", "EXDATE", "RDATE")) else f"RRULE:{r}"
                                    for r in rules]

    # The model cannot tell where an event landed unless we say so — "primary" on
    # this account is Transition — so `landed_on` reports the real destination.
    try:
        result = target_service.events().insert(
            calendarId=target_cal_id, body=event_body,
        ).execute()
        start_dt, is_allday = _parse_event_time(result["start"])
        end_dt, _ = _parse_event_time(result["end"])
        return {
            "id": result.get("id"),
            "summary": result.get("summary"),
            "start": start_dt,
            "end": end_dt,
            "is_allday": is_allday,
            "link": result.get("htmlLink", ""),
            "account": target_account,
            "calendar": landed_on,
            "calendar_id": target_cal_id,
            **({"recurrence": result["recurrence"]} if result.get("recurrence") else {}),
        }
    except Exception as e:
        raise CalendarError(f"Google Calendar rejected the event: {e}") from e


def create_allday_event(
    summary: str,
    date: str,
    calendar_name: str | None = None,
    account: str | None = None,
    description: str = "",
    end_date: str | None = None,
) -> dict | None:
    """Create an all-day event on `date` (YYYY-MM-DD), through `end_date` inclusive.

    Google's all-day end date is EXCLUSIVE — a one-day event on the 20th ends on
    the 21st. Passing the same date for both made every all-day event fail
    ("The specified time range is empty"), and the failure was swallowed.
    """
    target_cal_id, target_account, target_service, landed_on = _write_target(calendar_name, account)

    last = datetime.strptime(end_date or date, "%Y-%m-%d").date()
    event_body = {
        "summary": summary,
        "start": {"date": date},
        "end": {"date": (last + timedelta(days=1)).isoformat()},
    }
    if description:
        event_body["description"] = description

    try:
        result = target_service.events().insert(
            calendarId=target_cal_id, body=event_body,
        ).execute()
        start_dt, _ = _parse_event_time(result["start"])
        return {
            "id": result.get("id"),
            "summary": result.get("summary"),
            "start": start_dt,
            "is_allday": True,
            "link": result.get("htmlLink", ""),
            "account": target_account,
            "calendar": landed_on,
            "calendar_id": target_cal_id,
        }
    except Exception as e:
        raise CalendarError(f"Google Calendar rejected the all-day event: {e}") from e


# ---------------------------------------------------------------------------
# Event management
# ---------------------------------------------------------------------------
def delete_event(event_id: str, calendar_id: str | None = None, account: str | None = None) -> dict:
    """Delete a calendar event by ID, finding its calendar automatically.

    `calendar_id` is now only a hint. Previously it defaulted to "primary" and
    the model rarely overrode it, so deleting anything in a sub-calendar failed —
    silently, with a bare False the model could not learn anything from.
    """
    hit = resolve_event(event_id, account, calendar_hint=calendar_id)
    if not hit:
        return {"success": False,
                "error": f"No event with id {event_id!r} exists on any calendar. "
                         "Fetch a current event_id with get_calendar_range or "
                         "find_event before deleting."}
    ev = hit["event"]
    when = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date") or ""
    try:
        hit["service"].events().delete(
            calendarId=hit["calendar_id"], eventId=event_id,
        ).execute()
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "summary": ev.get("summary", ""), "start": when,
            "calendar": hit["calendar"], "account": hit["label"]}


# Fields Google assigns to an event, or that tie it to its original calendar.
# Replaying any of these into a fresh insert either errors or silently re-links
# the copy to the event it came from.
_UNCOPYABLE_EVENT_FIELDS = (
    "id", "etag", "iCalUID", "sequence", "htmlLink", "created", "updated",
    "creator", "organizer", "hangoutLink", "conferenceData", "recurringEventId",
    "originalStartTime", "attendees", "eventType",
)


def move_event(event_id: str, source_calendar: str | None, dest_calendar: str,
               account: str | None = None) -> dict:
    """Move an event to another calendar by re-creating it there and deleting the original.

    Google's events.move only works within one account, and it is a third code
    path the model has to reason about on top of create and delete. Since the
    user says "move this to Appointments" — and the model will reach for the move
    tool whenever they do — this does the create+delete itself rather than
    exposing that distinction. One path, same behaviour within an account and
    across accounts.

    Create comes before delete deliberately: if the insert fails, the original is
    still there. The reverse order can lose the event outright.

    Nothing is emailed to anyone (sendUpdates="none") — a move should not read as
    a cancellation followed by a fresh invitation.
    """
    src = resolve_event(event_id, account, calendar_hint=source_calendar)
    if not src:
        return {"success": False,
                "error": f"No event with id {event_id!r} exists on any calendar. "
                         "Fetch a current event_id with find_event or get_calendar_range."}

    dest = find_calendar_id(dest_calendar, src["label"]) or find_calendar_id(dest_calendar)
    if not dest:
        return {"success": False,
                "error": f"No calendar named {dest_calendar!r}. Call list_calendars "
                         "to see the real names."}
    dest_id, dest_label, dest_service = dest

    if dest_id == src["calendar_id"] and dest_label == src["label"]:
        return {"success": True, "id": event_id,
                "summary": src["event"].get("summary", ""),
                "destination": dest_calendar, "account": dest_label,
                "note": f"Already on {dest_calendar}; nothing to do."}

    event = src["event"]
    body = {k: v for k, v in event.items()
            if k not in _UNCOPYABLE_EVENT_FIELDS and v is not None}
    body.pop("status", None)            # a "cancelled" source must not be recreated as such

    try:
        created = dest_service.events().insert(
            calendarId=dest_id, body=body, sendUpdates="none",
        ).execute()
    except Exception as e:
        return {"success": False,
                "error": f"Could not create the event on {dest_calendar!r}: {e}. "
                         "The original was left untouched."}

    try:
        src["service"].events().delete(
            calendarId=src["calendar_id"], eventId=event_id, sendUpdates="none",
        ).execute()
    except Exception as e:
        return {"success": False,
                "error": f"Created a copy on {dest_calendar} (id {created.get('id')}) but "
                         f"could not delete the original on {src['calendar']}: {e}. "
                         "The event now exists TWICE — delete one of them.",
                "id": created.get("id"), "duplicate": True}

    result = {"success": True, "id": created.get("id"),
              "summary": created.get("summary", ""),
              "from": src["calendar"], "destination": dest_calendar,
              "account": dest_label,
              "note": "Re-created on the destination calendar, so the event has a new id."}
    if event.get("attendees"):
        result["warning"] = (f"{len(event['attendees'])} attendee(s) were NOT carried over, "
                             "so nobody was re-invited or notified.")
    if event.get("recurringEventId"):
        result["warning"] = ("This was one occurrence of a recurring series. That single "
                             "occurrence was moved and is now a standalone event; the rest "
                             "of the series is unchanged.")
    return result


def quick_add_event(text: str, calendar_name: str | None = None, account: str | None = None) -> dict | None:
    """Create an event from natural language text.

    Examples: "Dinner with Michael Friday 7pm", "Meeting tomorrow 2-3pm"
    Google parses the text and creates the event.
    """
    target_cal_id, target_account, target_service, landed_on = _write_target(calendar_name, account)

    try:
        result = target_service.events().quickAdd(
            calendarId=target_cal_id,
            text=text,
        ).execute()
        start_dt, is_allday = _parse_event_time(result["start"])
        end_dt, _ = _parse_event_time(result["end"])
        return {
            "id": result.get("id"),
            "summary": result.get("summary", ""),
            "start": start_dt,
            "end": end_dt,
            "is_allday": is_allday,
            "link": result.get("htmlLink", ""),
            "account": target_account,
            "calendar": landed_on,
            "calendar_id": target_cal_id,
        }
    except Exception as e:
        raise CalendarError(f"Google Calendar rejected the quick-add: {e}") from e


def delete_calendar(calendar_name: str, account: str | None = None) -> bool:
    """Delete a sub-calendar. Cannot delete primary calendar."""
    found = find_calendar_id(calendar_name, account)
    if not found:
        print(f"[calendar] Calendar '{calendar_name}' not found")
        return False

    cal_id, label, service = found
    try:
        service.calendars().delete(calendarId=cal_id).execute()
        return True
    except Exception as e:
        print(f"[calendar] Error deleting calendar: {e}")
        return False


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------
def get_upcoming_events(hours_ahead: int = 24, max_results: int = 20) -> list[dict]:
    now = datetime.now(timezone.utc)
    return _fetch_events(now, now + timedelta(hours=hours_ahead), max_results)


def get_range_events(start_date: str, end_date: str, max_results: int = 100) -> list[dict]:
    """Events on the user's local days start_date..end_date (YYYY-MM-DD), inclusive."""
    start, end = local_range(start_date, end_date)
    return _fetch_events(start, end, max_results=max_results)


def get_today_events() -> list[dict]:
    start = local_day_start(datetime.now(timezone.utc))
    end = local_day_start(start.date() + timedelta(days=1))
    return _fetch_events(start.astimezone(timezone.utc), end.astimezone(timezone.utc))


def get_week_events() -> list[dict]:
    """Today through the end of the local Sunday."""
    start = local_day_start(datetime.now(timezone.utc))
    end = local_day_start(start.date() + timedelta(days=7 - start.weekday()))
    return _fetch_events(start.astimezone(timezone.utc), end.astimezone(timezone.utc), max_results=100)


def get_next_week_events() -> list[dict]:
    """Next Monday through Sunday, local."""
    today = local_day_start(datetime.now(timezone.utc))
    start = local_day_start(today.date() + timedelta(days=7 - today.weekday()))
    end = local_day_start(start.date() + timedelta(days=7))
    return _fetch_events(start.astimezone(timezone.utc), end.astimezone(timezone.utc), max_results=100)


def get_current_event() -> dict | None:
    """Return the event currently in progress, if any."""
    now = datetime.now(timezone.utc)
    events = _fetch_events(now - timedelta(hours=4), now + timedelta(minutes=1), max_results=10)
    for event in events:
        # Skip all-day events — they're rarely what "current event" means,
        # and including them would mark every day as "currently in" a birthday.
        if event.get("is_allday"):
            continue
        if event["start"] <= now <= event["end"]:
            return event
    return None


def format_events_for_context(events: list[dict]) -> str:
    if not events:
        return "(no events)"
    tz = user_tz()
    lines = []
    for e in events:
        start = e["start"]
        if e.get("is_allday"):
            # All-day starts are UTC midnight of the date itself; converting
            # them would roll back to the previous evening.
            time_str = start.strftime("%a %b %d") + " (all-day)"
            last_day = (e.get("end") or start) - timedelta(days=1)
            if last_day.date() > start.date():
                time_str = f"{start:%a %b %d}–{last_day:%a %b %d} (all-day)"
        else:
            local = start.astimezone(tz)
            time_str = local.strftime("%a %b %d %-I:%M %p")
            if e.get("end"):
                end = e["end"].astimezone(tz)
                time_str += "–" + (end.strftime("%-I:%M %p") if end.date() == local.date()
                                   else end.strftime("%a %-I:%M %p"))

        line = f"- {time_str}: {e['summary']}"
        if e.get("calendar") and e["calendar"] != e.get("summary"):
            line += f" ({e['calendar']})"
        if e.get("location"):
            line += f" @ {e['location']}"
        if e.get("account"):
            line += f" [{e['account']}]"
        # IDs Claude needs to update/delete/move this event
        if e.get("id"):
            line += f" [event_id:{e['id']}]"
        if e.get("calendar_id") and e["calendar_id"] != "primary":
            line += f" [calendar_id:{e['calendar_id']}]"
        lines.append(line)
    return "\n".join(lines)


def format_day_for_user(events: list[dict]) -> str:
    """One day's events for a person to read: times, titles, places.

    format_events_for_context is for the model and carries ids, accounts and
    calendar names; none of that belongs in a text message.
    """
    if not events:
        return "Nothing on the calendar."
    tz = user_tz()
    lines = []
    for e in events:
        if e.get("is_allday"):
            when = "All day"
        else:
            local = e["start"].astimezone(tz)
            when = local.strftime("%-I:%M")
            if e.get("end"):
                end = e["end"].astimezone(tz)
                if local.strftime("%p") != end.strftime("%p"):
                    when += local.strftime(" %p").lower()
                when += "–" + end.strftime("%-I:%M %p").lower()
            else:
                when += local.strftime(" %p").lower()
        line = f"- {when}  {e['summary']}"
        if e.get("location"):
            # Just the place name, not the full street address
            line += f" ({e['location'].split(',')[0].strip()})"
        lines.append(line)
    return "\n".join(lines)


def format_calendars_for_context(calendars: list[dict]) -> str:
    if not calendars:
        return "(no calendars)"
    lines = []
    for c in calendars:
        primary = " (primary)" if c["primary"] else ""
        color = f" {{{c['color']}}}" if c.get("color") else ""
        lines.append(f"- {c['name']}{primary} [{c['account']}]{color}")
    return "\n".join(lines)


if __name__ == "__main__":
    print("Calendar Integration Test")
    print("=" * 55)

    cals = list_calendars()
    if cals:
        print(f"\nAll calendars ({len(cals)}):")
        print(format_calendars_for_context(cals))
    else:
        print("\nNo calendars found.")

    print()
    events = get_today_events()
    if events:
        print(f"Today's events ({len(events)}):")
        print(format_events_for_context(events))
    else:
        print("No events today.")
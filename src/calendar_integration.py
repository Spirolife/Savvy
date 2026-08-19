"""
Google Calendar integration — multi-account, multi-calendar.
Reads from ALL sub-calendars (appointments, chores, etc.) and can create events.

Datetime convention:
    All event start/end values stored on returned dicts are timezone-aware
    `datetime` objects (UTC for all-day events, original offset preserved for
    timed events). All-day events are flagged via `is_allday=True`.
    Callers should not call `fromisoformat` on these values themselves.
"""

from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build

from google_auth import get_all_credentials


def _get_services() -> list[tuple[str, object]]:
    services = []
    for label, creds in get_all_credentials().items():
        try:
            svc = build("calendar", "v3", credentials=creds)
            services.append((label, svc))
        except Exception:
            pass
    return services


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
                })
        except Exception as e:
            print(f"[calendar] Error listing calendars for {label}: {e}")
    return all_calendars


def find_calendar_id(name_query: str, account: str | None = None) -> tuple[str, str, object] | None:
    """Find a calendar by name substring. Returns (calendar_id, account_label, service)."""
    query = name_query.lower()
    for label, service in _get_services():
        if account and label != account:
            continue
        try:
            result = service.calendarList().list().execute()
            for cal in result.get("items", []):
                if query in cal.get("summary", "").lower():
                    return (cal["id"], label, service)
        except Exception:
            pass
    return None


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
) -> dict | None:
    """Create a calendar event.

    Args:
        summary: Event title
        start_time: ISO format datetime (e.g. "2026-03-25T14:00:00-04:00")
        end_time: ISO format datetime
        calendar_name: Sub-calendar name (e.g. "appointments", "chores").
                       Uses primary if not specified.
        account: Account label ("personal", "northeastern"). Uses first if not specified.
        description: Optional event description
        location: Optional location
    """
    target_cal_id = "primary"
    target_service = None

    if calendar_name:
        found = find_calendar_id(calendar_name, account)
        if found:
            target_cal_id, _, target_service = found

    if not target_service:
        services = _get_services()
        if account:
            services = [(l, s) for l, s in services if l == account]
        if not services:
            return None
        _, target_service = services[0]

    event_body = {
        "summary": summary,
        "start": {"dateTime": start_time},
        "end": {"dateTime": end_time},
    }
    if description:
        event_body["description"] = description
    if location:
        event_body["location"] = location

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
        }
    except Exception as e:
        print(f"[calendar] Error creating event: {e}")
        return None


def create_allday_event(
    summary: str,
    date: str,
    calendar_name: str | None = None,
    account: str | None = None,
    description: str = "",
) -> dict | None:
    """Create an all-day event. date format: "YYYY-MM-DD"."""
    target_cal_id = "primary"
    target_service = None

    if calendar_name:
        found = find_calendar_id(calendar_name, account)
        if found:
            target_cal_id, _, target_service = found

    if not target_service:
        services = _get_services()
        if account:
            services = [(l, s) for l, s in services if l == account]
        if not services:
            return None
        _, target_service = services[0]

    event_body = {
        "summary": summary,
        "start": {"date": date},
        "end": {"date": date},
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
        }
    except Exception as e:
        print(f"[calendar] Error creating all-day event: {e}")
        return None


# ---------------------------------------------------------------------------
# Event management
# ---------------------------------------------------------------------------
def delete_event(event_id: str, calendar_id: str = "primary", account: str | None = None) -> bool:
    """Delete a calendar event by ID."""
    for label, service in _get_services():
        if account and label != account:
            continue
        try:
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            return True
        except Exception:
            continue
    return False


def move_event(event_id: str, source_calendar_id: str, dest_calendar_id: str, account: str | None = None) -> dict | None:
    """Move an event from one calendar to another."""
    for label, service in _get_services():
        if account and label != account:
            continue
        try:
            result = service.events().move(
                calendarId=source_calendar_id,
                eventId=event_id,
                destination=dest_calendar_id,
            ).execute()
            return {
                "id": result.get("id"),
                "summary": result.get("summary", ""),
                "destination": dest_calendar_id,
            }
        except Exception as e:
            print(f"[calendar] Error moving event: {e}")
    return None


def quick_add_event(text: str, calendar_name: str | None = None, account: str | None = None) -> dict | None:
    """Create an event from natural language text.

    Examples: "Dinner with Michael Friday 7pm", "Meeting tomorrow 2-3pm"
    Google parses the text and creates the event.
    """
    target_cal_id = "primary"
    target_service = None

    if calendar_name:
        found = find_calendar_id(calendar_name, account)
        if found:
            target_cal_id, _, target_service = found

    if not target_service:
        services = _get_services()
        if account:
            services = [(l, s) for l, s in services if l == account]
        if not services:
            return None
        _, target_service = services[0]

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
        }
    except Exception as e:
        print(f"[calendar] Error quick-adding event: {e}")
        return None


def check_freebusy(start_date: str, end_date: str, account: str | None = None) -> list[dict]:
    """Check free/busy times for a date range.

    Returns list of busy periods with aware datetimes:
        [{"start": <aware datetime>, "end": <aware datetime>}, ...]
    """
    for label, service in _get_services():
        if account and label != account:
            continue
        try:
            cal_list = service.calendarList().list().execute()
            primary_id = None
            for cal in cal_list.get("items", []):
                if cal.get("primary"):
                    primary_id = cal["id"]
                    break
            primary_id = primary_id or "primary"

            body = {
                "timeMin": f"{start_date}T00:00:00Z",
                "timeMax": f"{end_date}T23:59:59Z",
                "items": [{"id": primary_id}],
            }
            result = service.freebusy().query(body=body).execute()

            busy_times = []
            for cal_id, cal_data in result.get("calendars", {}).items():
                for busy in cal_data.get("busy", []):
                    try:
                        busy_times.append({
                            "start": datetime.fromisoformat(busy["start"].replace("Z", "+00:00")),
                            "end": datetime.fromisoformat(busy["end"].replace("Z", "+00:00")),
                        })
                    except (ValueError, TypeError):
                        continue
            return busy_times
        except Exception as e:
            print(f"[calendar] Error checking freebusy: {e}")
    return []


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


def get_today_events() -> list[dict]:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return _fetch_events(start, start + timedelta(days=1))


def get_week_events() -> list[dict]:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days_left = 6 - now.weekday()
    return _fetch_events(start, start + timedelta(days=max(days_left + 1, 1)), max_results=100)


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
    lines = []
    for e in events:
        start = e["start"]
        if e.get("is_allday"):
            time_str = start.strftime("%a %b %d") + " (all-day)"
        else:
            time_str = start.strftime("%a %b %d %-I:%M %p")

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


def format_calendars_for_context(calendars: list[dict]) -> str:
    if not calendars:
        return "(no calendars)"
    lines = []
    for c in calendars:
        primary = " (primary)" if c["primary"] else ""
        lines.append(f"- {c['name']}{primary} [{c['account']}]")
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
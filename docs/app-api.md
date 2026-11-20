# Savvy app API (v1)

The contract between the Android app (`android/`) and the Savvy server on the PC
(`src/app_api/`). Both sides build against this file. Change it only with a note
in your final report, and keep both sides in step.

## Transport and auth

- HTTP, JSON bodies, UTF-8. Base URL is whatever the user enters when pairing,
  e.g. `http://my-pc.tailnet-name.ts.net:8765` or `http://100.x.y.z:8765`.
  Reached over Tailscale, which encrypts the link; the server binds to
  `app_api_host` (default `127.0.0.1`) and `app_api_port` (default `8765`) from
  `credentials/config.json`.
- Every endpoint except `POST /v1/pair` and `GET /v1/ping` needs
  `Authorization: Bearer <device token>`. A missing or unknown token → `401`
  `{"error": "unpaired"}`; the app then returns to the pairing screen.
- Errors: non-2xx with `{"error": "<machine code>", "message": "<for humans>"}`.
- Times: ISO 8601 with offset (`2026-11-16T10:00:00-05:00`). Dates `YYYY-MM-DD`.
- Field names are `snake_case` on the wire. Optional fields may be absent or `null`.

## Pairing

`python src/app_server.py --pair` (and the tray's "Pair phone…" item) prints a
6-digit code valid for 10 minutes.

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/v1/ping` | | `{"ok": true, "name": "Savvy", "version": "1"}` |
| POST | `/v1/pair` | `{"code": "123456", "device_name": "Pixel 8"}` | `{"token": "<opaque>"}` · `403 bad_code` · `429 too_many_attempts` (5 wrong codes in 10 minutes burn the code; make a new one) |

Tokens are stored hashed in `credentials/app_devices.json` (0600).

## Status

`GET /v1/status` →
```json
{
  "google": [{"label": "personal", "email": "a@gmail.com", "connected": true, "reason": null}],
  "todoist": true,
  "ollama": true,
  "signal_fallback": false
}
```
`connected: false` with `reason` ("the login expired or was revoked", "no saved login") when an account needs signing in.

## Google accounts (native sign-in)

The phone signs in with Android's `AuthorizationClient`, requesting offline
access for the server's **Web** OAuth client, and posts the one-time server auth
code. The PC exchanges it with the Web client secret
(`credentials/web_client.json`, a "Web application" client downloaded from
Google Cloud) and saves `credentials/token_<label>.json` exactly like
`google_auth._write_token`, with the same identity checks as
`google_auth.complete_signin` (an account already saved under another label, or
a different account than the label had, is refused).

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/v1/google/config` | | `{"web_client_id": "...apps.googleusercontent.com", "scopes": ["https://www.googleapis.com/auth/calendar", ...]}` · `503 no_web_client` when the file is missing |
| POST | `/v1/google/connect` | `{"label": "school", "server_auth_code": "4/0A..."}` | `{"ok": true, "email": "...", "message": "'school' is signed in again as ..."}` · `409 wrong_account` / `409 label_clash` / `400 exchange_failed` |
| DELETE | `/v1/google/{label}` | | `{"ok": true}` (deletes the token file) |

Labels are `[a-z0-9_-]{1,32}`.

## Today

`GET /v1/today?date=YYYY-MM-DD` (default today) →
```json
{
  "date": "2026-11-16",
  "next": {"event": Event, "leave_by": "...", "travel_minutes": 22, "travel_mode": "transit"},
  "events": [Event],
  "tasks": [Task],
  "reminders": [Reminder],
  "rest": {"suggested": true, "day": "2026-11-19", "message": "..."},
  "signin_needed": ["school"]
}
```
`next`, `rest` may be `null`. Travel comes from `travel.py` when an event has a
location and a Maps key is configured; otherwise `leave_by`/`travel_minutes` are null.

- **Event** `{"id", "account", "calendar", "title", "start", "end", "all_day": bool, "location": str|null, "color": "#RRGGBB"|null}`
- **Task** `{"id", "title", "project": str|null, "due": "YYYY-MM-DD"|null, "priority": 1-4, "completed": bool}` (priority as Todoist's app shows it: 1 = urgent … 4 = normal)
- **Reminder** `{"id", "text", "at"}`

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/v1/tasks/{id}/complete` | | `{"ok": true}` |
| POST | `/v1/tasks/{id}/reopen` | | `{"ok": true}` |
| POST | `/v1/rest/plan` | `{"day": "2026-11-19"}` | `{"ok": true, "message": "..."}` (books an all-day "Rest day" on the Rest calendar via `create_allday_event`, as the model does; `502 calendar_error` with the reason on failure) |

## Chat

`POST /v1/chat` `{"message": "..."}` → `text/event-stream`. One turn at a time;
a second concurrent request gets `409 busy`. Events (each `data:` is JSON):

| `event:` | data |
| --- | --- |
| `text` | `{"delta": "..."}` streamed reply text |
| `tool` | `{"name": "move_event", "state": "read"\|"done"\|"denied"\|"error", "detail": "...", "warning": str\|null}` (from `tool_diagnostics.describe_tool_result`) |
| `confirm` | `{"id": "c1", "name": "send_email", "level": 2, "args": {...}}` the turn pauses until answered |
| `done` | `{"reply": "full final reply"}` |
| `error` | `{"message": "..."}` |

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/v1/chat/confirm` | `{"id": "c1", "decision": "allow"\|"deny"}` | `{"ok": true}` · `404` when nothing is waiting. Unanswered after 5 minutes → deny. |
| GET | `/v1/chat/history?limit=40` | | `[{"role": "user"\|"assistant", "content": "...", "ts": "...", "surface": "app"\|"signal"\|"desktop"}]` oldest first |

Server side: `core.respond(..., surface="app")` with a `Memory(session_id="app")`;
markdown replies (like the desktop). A pasted Google sign-in redirect is handled
by `core.finish_pasted_signin` first, as on the other surfaces.

## Inbox

`GET /v1/inbox?account=personal|school|all&q=&limit=30` →
`{"summary": str|null, "unread": {"personal": 3}, "messages": [Mail]}`

- **Mail** `{"id", "thread_id", "account", "from", "subject", "snippet", "date", "unread": bool, "starred": bool, "important": bool}`
- Without `q`, `messages` and `unread` cover only mail that is or may be important: in the inbox and starred, or marked Important by Gmail outside the Promotions, Social and Forums tabs. A `q` search covers all mail.
- `summary` is a one-to-two sentence model summary of important unread mail, cached 15 minutes; may be null.

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/v1/inbox/{account}/threads/{thread_id}` | | `{"subject", "messages": [{"id", "from", "to", "date", "body_text"}]}` |
| POST | `/v1/inbox/{account}/messages/{id}/action` | `{"action": "archive"\|"star"\|"unstar"\|"read"\|"unread"\|"trash"}` | `{"ok": true}` |

Sending and drafting happen through chat, so they keep the confirm sheet.

## Plan

`GET /v1/plan` →
```json
{
  "goals": [{"id": 1, "title": "...", "category": "...", "target": "...", "status": "active", "progress_note": "...", "last_logged": "..."|null}],
  "meals": {"week": "2026-11-16", "diet": "...", "items": [{"id": 3, "main": "...", "kind": "meal", "sides": ["..."], "day": "Mon"|null}]},
  "projects": [{"id": "...", "name": "School", "open_count": 7}]
}
```

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/v1/goals/{id}/progress` | `{"note": "...", "value": "..."}` | `{"ok": true}` |
| POST | `/v1/meals` | `{"main": "...", "kind": "meal", "sides": []}` | `{"ok": true, "id": 4}` · `400` with the meal-rule message |
| DELETE | `/v1/meals/{id}` | | `{"ok": true}` |
| GET | `/v1/projects/{id}/tasks` | | `[Task]` |

## Diary and notes

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/v1/diary?days=14` | | `[{"id", "type": "bod"\|"eod"\|"bad_day"\|"entry", "text", "ts"}]` newest first |
| POST | `/v1/diary` | `{"type": "bod"\|"eod"\|"bad_day"\|"entry", "text": "..."}` | `{"ok": true}` |
| GET | `/v1/notes` | | `[{"name", "updated"}]` |
| GET | `/v1/notes/{name}` | | `{"name", "text"}` |

## Settings and memory

`GET /v1/settings` / `PUT /v1/settings` (partial update; unknown keys `400`) →
```json
{
  "bod_time": "08:30", "eod_time": "22:00",
  "checkin_times": ["09:30", "13:00", "16:30"],
  "weekly_review_day": "Sunday", "weekly_review_time": "18:00",
  "quiet_hours_start": 22, "quiet_hours_end": 8,
  "rest_day_warnings": true,
  "signal_fallback": true
}
```
Writes go to `credentials/config.json` (same keys the scheduler reads), atomically.

| Method | Path | Response |
| --- | --- | --- |
| GET | `/v1/memory/facts?q=` | `[{"id", "fact", "type", "updated"}]` |
| DELETE | `/v1/memory/facts/{id}` | `{"ok": true}` |
| GET | `/v1/confirm-levels` | `{"send_email": 2, ...}` (read only in v1) |

## Notifications

Everything the scheduler and alerts used to send over Signal goes through
`src/notify.py` `deliver(title, body, kind)`: it records the notification, pushes
it to registered devices with Firebase Cloud Messaging when configured, and still
sends it over Signal while `signal_fallback` is true.

- **Notification** `{"id": 42, "ts": "...", "kind": "bod"|"eod"|"checkin"|"weekly"|"reminder"|"meal"|"rest"|"fact_check"|"alert"|"signin", "title": "...", "body": "..."}`

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/v1/devices/fcm` | `{"fcm_token": "..."}` | `{"ok": true}` |
| GET | `/v1/notifications?after_id=0&limit=50` | | `[Notification]` oldest first (the app polls this when FCM isn't set up) |

FCM is optional on both sides: the server needs `credentials/firebase_service_account.json`,
the app needs `android/app/google-services.json`. Without them the app polls.
The FCM message is data-only: `{"id", "kind", "title", "body"}` (all strings). A
body over ~3 KB is clipped to fit FCM's 4 KB limit and the message then also has
`"truncated": "1"`; the app reads the full text from `/v1/notifications`.

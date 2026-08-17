"""Level 2 — Mail, Calendar, Notes and Reminders.

All four ship AppleScript dictionaries, so this is deterministic and free: no
UI automation, no accessibility, no key.

## Why these are separate tools

The measured rule is to merge tools the model confuses (RELIABILITY.md), and
`network_control` had to be folded into `system_control` because people phrase
Wi-Fi as a system setting. These four are different: "send an email", "add a
reminder" and "what's on my calendar" are semantically distinct requests, so
they get distinct tools — and the selection benchmark will say whether that
holds rather than it being assumed.

The old read-only `app_info` is replaced by these. Keeping it would have split
reads and writes for the same apps across two tools, which is exactly the
overlap that cost 9 points last time.

## Mail never sends

`draft` composes a message and leaves it **open and unsent**. Sending on
someone's behalf is irreversible and easy to get subtly wrong — wrong
recipient, wrong tone, half-finished thought. The last step stays a human one.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from macman.agent.tools.schema import tool

from macman.agent.tools.applescript import run as run_applescript
from macman.agent.tools.registry import _guarded, require_confirmation

MAX_ITEMS = 25


def _escape(text: str) -> str:
    """Escape a value for embedding in an AppleScript string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _applescript_date(moment: datetime, variable: str) -> str:
    """Build a date by setting components, rather than parsing a string.

    AppleScript's `date "..."` coercion follows system locale, so the same
    literal means different things on different Macs. Setting each component
    is unambiguous everywhere.
    """
    return (
        f'set {variable} to current date\n'
        f'set year of {variable} to {moment.year}\n'
        f'set month of {variable} to {moment.month}\n'
        f'set day of {variable} to {moment.day}\n'
        f'set hours of {variable} to {moment.hour}\n'
        f'set minutes of {variable} to {moment.minute}\n'
        f'set seconds of {variable} to 0\n'
    )


def _parse_when(raw: str) -> datetime | None:
    """Accept ISO-8601, with or without a time."""
    text = raw.strip().replace("/", "-")
    for pattern in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Mail
# --------------------------------------------------------------------------- #


@tool
def mail_control(action: str, to: str = "", subject: str = "", body: str = "") -> str:
    """Read your inbox or compose an email draft in Mail.

    Args:
        action: One of "unread_count", "list_recent", "draft".
        to: Recipient address, for "draft".
        subject: Subject line, for "draft".
        body: Message text, for "draft".
    """
    def run() -> str:
        key = action.strip().lower()

        if key == "unread_count":
            result = run_applescript(
                'tell application "Mail" to return unread count of inbox')
            return (f"{result.output.strip()} unread messages." if result.ok
                    else f"Could not ask Mail: {result.for_model()[:120]}")

        if key == "list_recent":
            result = run_applescript(
                f'tell application "Mail"\n'
                f'  set output to ""\n'
                f'  set n to (count of messages of inbox)\n'
                f'  if n > {MAX_ITEMS} then set n to {MAX_ITEMS}\n'
                f'  repeat with i from 1 to n\n'
                f'    set m to message i of inbox\n'
                f'    set output to output & (sender of m) & " — " & '
                f'(subject of m) & linefeed\n'
                f'  end repeat\n'
                f'  return output\n'
                f'end tell', timeout=45)
            return (result.output.strip() or "Inbox is empty." if result.ok
                    else f"Could not read the inbox: {result.for_model()[:120]}")

        if key == "draft":
            if not to.strip() or not subject.strip():
                return "A draft needs at least a recipient and a subject."
            # Composed visible and unsent on purpose — see the module docstring.
            result = run_applescript(
                f'tell application "Mail"\n'
                f'  set msg to make new outgoing message with properties '
                f'{{subject:"{_escape(subject)}", content:"{_escape(body)}", '
                f'visible:true}}\n'
                f'  tell msg to make new to recipient at end of to recipients '
                f'with properties {{address:"{_escape(to.strip())}"}}\n'
                f'  activate\n'
                f'end tell', timeout=30)
            return (f"Drafted an email to {to.strip()} — it's open in Mail, "
                    f"unsent. Review and send it yourself." if result.ok
                    else f"Could not create the draft: {result.for_model()[:120]}")

        return (f"Unknown action {action!r}. Choose one of: unread_count, "
                f"list_recent, draft.")

    return _guarded("applescript", {"mail_control": action, "to": to}, run)


# --------------------------------------------------------------------------- #
# Calendar
# --------------------------------------------------------------------------- #


@tool
def calendar_control(action: str, title: str = "", when: str = "",
                     duration_minutes: int = 60) -> str:
    """See what's on your calendar, or add an event.

    Args:
        action: One of "today", "upcoming", "create_event".
        title: Event title, for "create_event".
        when: Start time in ISO format, e.g. "2026-08-20T15:00", for
            "create_event".
        duration_minutes: Event length. Defaults to 60.
    """
    def run() -> str:
        key = action.strip().lower()

        if key in {"today", "upcoming"}:
            days = 1 if key == "today" else 7
            start = datetime.now().replace(hour=0, minute=0)
            end = start + timedelta(days=days)
            result = run_applescript(
                f'{_applescript_date(start, "startDate")}'
                f'{_applescript_date(end, "endDate")}'
                f'tell application "Calendar"\n'
                f'  set output to ""\n'
                f'  repeat with c in calendars\n'
                f'    repeat with e in (every event of c whose start date is '
                f'greater than startDate and start date is less than endDate)\n'
                f'      set output to output & (summary of e) & " — " & '
                f'((start date of e) as string) & linefeed\n'
                f'    end repeat\n'
                f'  end repeat\n'
                f'  return output\n'
                f'end tell', timeout=60)
            if not result.ok:
                return f"Could not read the calendar: {result.for_model()[:120]}"
            text = result.output.strip()
            window = "today" if days == 1 else "the next 7 days"
            return text or f"Nothing scheduled for {window}."

        if key == "create_event":
            if not title.strip():
                return "An event needs a title."
            moment = _parse_when(when)
            if moment is None:
                return ("Give the start time in ISO format, e.g. "
                        "2026-08-20T15:00.")
            if not require_confirmation(
                    "adds an event to your calendar",
                    f"{title.strip()} at {moment:%d %b %Y %H:%M}"):
                return "Refused by the owner — no event was created."
            finish = moment + timedelta(minutes=max(1, duration_minutes))
            result = run_applescript(
                f'{_applescript_date(moment, "startDate")}'
                f'{_applescript_date(finish, "endDate")}'
                f'tell application "Calendar"\n'
                f'  tell calendar 1\n'
                f'    make new event with properties {{summary:"'
                f'{_escape(title.strip())}", start date:startDate, '
                f'end date:endDate}}\n'
                f'  end tell\n'
                f'end tell', timeout=45)
            return (f"Added {title.strip()!r} on {moment:%d %b %Y at %H:%M}."
                    if result.ok
                    else f"Could not create the event: {result.for_model()[:120]}")

        return (f"Unknown action {action!r}. Choose one of: today, upcoming, "
                f"create_event.")

    return _guarded("applescript",
                    {"calendar_control": action, "title": title}, run)


# --------------------------------------------------------------------------- #
# Notes
# --------------------------------------------------------------------------- #


@tool
def notes_control(action: str, title: str = "", body: str = "") -> str:
    """Read, search or create notes in the Notes app.

    Args:
        action: One of "count", "list", "read", "create".
        title: Note title — the one to read, or the one to create.
        body: Note text, for "create".
    """
    def run() -> str:
        key = action.strip().lower()

        if key == "count":
            result = run_applescript(
                'tell application "Notes" to return count of notes')
            return (f"You have {result.output.strip()} notes." if result.ok
                    else f"Could not ask Notes: {result.for_model()[:120]}")

        if key == "list":
            result = run_applescript(
                f'tell application "Notes"\n'
                f'  set output to ""\n'
                f'  set n to (count of notes)\n'
                f'  if n > {MAX_ITEMS} then set n to {MAX_ITEMS}\n'
                f'  repeat with i from 1 to n\n'
                f'    set output to output & (name of note i) & linefeed\n'
                f'  end repeat\n'
                f'  return output\n'
                f'end tell', timeout=45)
            return (result.output.strip() or "You have no notes." if result.ok
                    else f"Could not list notes: {result.for_model()[:120]}")

        if key == "read":
            if not title.strip():
                return "Say which note to read."
            result = run_applescript(
                f'tell application "Notes" to return body of first note whose '
                f'name contains "{_escape(title.strip())}"', timeout=30)
            if not result.ok:
                return f"Could not find a note matching {title.strip()!r}."
            text = result.output.strip()
            return (text[:3000] + "\n\n[truncated]") if len(text) > 3000 else \
                   (text or "That note is empty.")

        if key == "create":
            if not title.strip():
                return "A note needs a title."
            result = run_applescript(
                f'tell application "Notes" to make new note with properties '
                f'{{name:"{_escape(title.strip())}", '
                f'body:"{_escape(body)}"}}', timeout=30)
            return (f"Created a note called {title.strip()!r}." if result.ok
                    else f"Could not create the note: {result.for_model()[:120]}")

        return f"Unknown action {action!r}. Choose one of: count, list, read, create."

    return _guarded("applescript", {"notes_control": action, "title": title}, run)


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #


@tool
def reminders_control(action: str, title: str = "", when: str = "") -> str:
    """See your reminders, add one, or mark one done.

    Args:
        action: One of "list", "create", "complete".
        title: The reminder text — the one to add, or the one to complete.
        when: Optional due time in ISO format, e.g. "2026-08-20T09:00".
    """
    def run() -> str:
        key = action.strip().lower()

        if key == "list":
            result = run_applescript(
                f'tell application "Reminders"\n'
                f'  set output to ""\n'
                f'  repeat with r in (reminders whose completed is false)\n'
                f'    set output to output & (name of r) & linefeed\n'
                f'  end repeat\n'
                f'  return output\n'
                f'end tell', timeout=45)
            return (result.output.strip() or "Nothing outstanding." if result.ok
                    else f"Could not read reminders: {result.for_model()[:120]}")

        if key == "create":
            if not title.strip():
                return "A reminder needs some text."
            properties = f'{{name:"{_escape(title.strip())}"}}'
            prelude = ""
            if when.strip():
                moment = _parse_when(when)
                if moment is None:
                    return "Give the due time in ISO format, e.g. 2026-08-20T09:00."
                prelude = _applescript_date(moment, "dueDate")
                properties = (f'{{name:"{_escape(title.strip())}", '
                              f'due date:dueDate}}')
            result = run_applescript(
                f'{prelude}tell application "Reminders" to make new reminder '
                f'with properties {properties}', timeout=30)
            return (f"Added a reminder: {title.strip()}." if result.ok
                    else f"Could not add it: {result.for_model()[:120]}")

        if key == "complete":
            if not title.strip():
                return "Say which reminder to complete."
            result = run_applescript(
                f'tell application "Reminders"\n'
                f'  set matches to (reminders whose completed is false and '
                f'name contains "{_escape(title.strip())}")\n'
                f'  if (count of matches) is 0 then return "__NONE__"\n'
                f'  set completed of item 1 of matches to true\n'
                f'  return name of item 1 of matches\n'
                f'end tell', timeout=30)
            if not result.ok:
                return f"Could not update it: {result.for_model()[:120]}"
            name = result.output.strip()
            return (f"No open reminder matching {title.strip()!r}."
                    if name == "__NONE__" else f"Marked {name!r} done.")

        return f"Unknown action {action!r}. Choose one of: list, create, complete."

    return _guarded("applescript",
                    {"reminders_control": action, "title": title}, run)


#: Mail, Calendar, Notes and Reminders — replaces the old read-only `app_info`.
PERSONAL_TOOLS = [mail_control, calendar_control, notes_control, reminders_control]

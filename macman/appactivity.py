"""What MACman did, assembled for the Activity view.

Reads `audit.jsonl` and nothing else. **No new data is captured for this
view**, which is the whole design decision: an activity log that records more
than the audit log already does would be a second copy of your data, created
for the sake of reassuring you about the first one.

## What the log already holds, and why that is the right amount

* **The task, in your words** — you asked it; seeing it back is the point.
* **Tool names and their arguments**, minus content. `notes_control` records
  `create` and the note's *title*, never its body; `mail_control` records the
  recipient, never the message. That exclusion is deliberate and predates this
  view.
* **Result hashes and byte counts, never results.** You can tell that two runs
  produced the same answer without the answer being stored.

So the honest summary of a task is: what you asked, which engine ran it, what
it touched, and whether anything left the Mac. That is what this returns.

## Silent must never mean invisible

A pre-approved cloud send happens without a dialog. It is flagged here anyway,
and differently from one you approved in the moment — the point of standing
permission is fewer interruptions, not less visibility.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from macman import config

#: Bytes read from the end of the log. The file is append-only and grows
#: forever; the view wants recent history, and reading it all would make
#: opening a window slower the longer MACman has been useful.
TAIL_BYTES = 512_000


def _tail_records(path, limit_bytes: int = TAIL_BYTES) -> Iterator[dict[str, Any]]:
    """Yield records from the end of the log, oldest first.

    A partial first line is discarded rather than parsed: seeking into the
    middle of a file lands mid-record, and a half-parsed entry is worse than a
    missing one.
    """
    if not path.exists():
        return
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit_bytes:
                handle.seek(size - limit_bytes)
                handle.readline()              # discard the partial line
            for raw in handle:
                try:
                    yield json.loads(raw.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


@dataclass
class Entry:
    """One thing that happened, as a person would describe it."""

    ts: float
    session: str
    kind: str                      # "task" | "security"
    title: str
    engine: str = ""
    detail: str = ""
    tools: list[str] = field(default_factory=list)
    #: "" when nothing left the Mac — the common case, and the one worth
    #: stating positively rather than by omission.
    egress: str = ""
    ok: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "session": self.session,
            "kind": self.kind,
            "title": self.title,
            "engine": self.engine,
            "detail": self.detail,
            "tools": self.tools,
            "egress": self.egress,
            "ok": self.ok,
        }


#: Security events worth showing, and how to say them. Anything not listed is
#: omitted rather than shown raw: a log line is not a sentence.
_SECURITY_EVENTS = {
    "auth_failed": ("Login code rejected", False),
    "code_suppressed": ("A login code was sent mid-session and ignored", True),
    "egress_refused": ("Refused to send data", True),
    "expired": ("Session expired", True),
    "authenticated": ("Session started", True),
    "denied_handle": ("Message from an unknown number, dropped", True),
}


def read(limit: int = 100) -> dict[str, Any]:
    """Recent activity, newest first, plus totals for today."""
    tasks: dict[str, Entry] = {}
    entries: list[Entry] = []

    midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    tasks_today = sent_today = 0

    for record in _tail_records(config.AUDIT_LOG):
        ts = float(record.get("ts", 0))
        session = str(record.get("session", ""))
        kind = record.get("kind", "")
        event = record.get("event", "")

        if event == "task_start":
            entry = Entry(
                ts=ts, session=session, kind="task",
                title=str(record.get("task", "")) or "(no task recorded)",
                engine=str(record.get("engine", "")),
            )
            # Keyed by session so later tool calls attach to the right task;
            # a second task in the same session replaces the first, which is
            # correct — they are shown as separate entries either way.
            tasks[session] = entry
            entries.append(entry)
            if ts >= midnight:
                tasks_today += 1

        elif kind == "tool_call":
            entry = tasks.get(session)
            if entry is not None:
                tool = str(record.get("tool", ""))
                # The typed tools all arrive as "bash" with the real name in
                # the arguments, so prefer the argument key when it exists.
                args = record.get("args") or {}
                named = next((key for key in args if key not in
                              {"extension", "contains", "destination", "path",
                               "to", "title", "source", "value", "name"}), tool)
                if named not in entry.tools:
                    entry.tools.append(named)

        elif kind == "tool_result":
            entry = tasks.get(session)
            if entry is not None and not record.get("ok", True):
                entry.ok = False

        elif event in {"egress_approved", "egress_pre_approved"}:
            summary = str(record.get("summary", "data"))
            label = ("sent after you approved it"
                     if event == "egress_approved"
                     else "sent without asking — a standing pre-approval")
            entry = tasks.get(session)
            if entry is not None:
                entry.egress = f"{summary} — {label}"
            else:
                entries.append(Entry(ts=ts, session=session, kind="security",
                                     title="Data sent to a cloud model",
                                     detail=f"{summary} — {label}"))
            if ts >= midnight:
                sent_today += 1

        elif event in _SECURITY_EVENTS:
            title, ok = _SECURITY_EVENTS[event]
            detail = str(record.get("reason") or record.get("result") or "")
            entries.append(Entry(ts=ts, session=session, kind="security",
                                 title=title, detail=detail, ok=ok))

    entries.sort(key=lambda item: item.ts, reverse=True)
    return {
        "entries": [entry.as_dict() for entry in entries[:limit]],
        "tasks_today": tasks_today,
        "sent_today": sent_today,
        "audit_path": str(config.AUDIT_LOG),
        # Stated in the UI so nobody assumes the view is the whole record.
        "note": ("Results are stored as hashes, not content. Note bodies and "
                 "message text are never written to this log."),
    }

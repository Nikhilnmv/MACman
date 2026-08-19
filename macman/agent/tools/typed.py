"""Typed tools for the on-device model.

Apple's ~3B model is good at *choosing* a tool and filling in typed arguments,
and bad at *authoring shell syntax*. Measured, not assumed — given raw `bash`
it produced `ls -l ~/Downloads | grep -v . | grep Pdf | wc -l` (which deletes
every non-empty line), `ls -1 Downloads | grep 'PDF'` (relative path, exit 1),
and `df -h /Users/me/Downloads` (wrong command, invented path) across three
consecutive runs — then reported confident file counts from all three. Given a
*typed* tool it answered correctly 4/4.

So the local engine gets these instead of a shell. Python builds every command
from validated arguments, which means:

* **A malformed command is impossible**, not merely discouraged.
* **Paths are checked before use**, so `DENIED_READ_PATHS` is enforced by
  construction rather than by pattern-matching a string the model wrote.
* **Failures are visible.** Each tool reports an empty or failed result as
  such, because the model demonstrably will not check an exit code itself.

Claude keeps raw `bash` — it writes correct commands and needs the generality.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from macman.agent.tools.schema import tool

from macman import config
from macman.agent.tools import shell as shell_tool
from macman.agent.tools.applescript import run as run_applescript
from macman.security.paths import within as path_within
# Same gate the cloud engine's tools use: tier check, guard verdict, audit.
from macman.agent.tools.registry import _guarded

MAX_ROWS = 200


class PathRefused(ValueError):
    """Raised when an argument names a path the tools must not read."""


#: Whether a resolved path falls inside a protected one. Lives in
#: `security/paths` because `security/egress` needs the identical check to
#: match cloud pre-approvals, and two copies of a rule this subtle would drift.
#: Kept as a module-level name here because the audit suite imports it.
_within = path_within


def _safe_folder(raw: str) -> Path:
    """Resolve a user-supplied path and refuse protected locations.

    Enforced here rather than left to the guard's regexes: the model supplies a
    *value*, not a command string, so this is an exact check rather than a
    pattern match that could be worded around.
    """
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = Path.home() / candidate  # "Downloads" means ~/Downloads
    resolved = candidate.resolve()

    for denied in config.DENIED_READ_PATHS:
        if _within(resolved, denied):
            raise PathRefused(f"{raw} is a protected location and cannot be read.")
    return resolved


def _describe_rows(rows: list[str], what: str, where: Path) -> str:
    """Render a result so an empty one cannot be mistaken for a small one."""
    if not rows:
        return f"No {what} found in {where}."
    shown = rows[:MAX_ROWS]
    body = "\n".join(shown)
    suffix = (f"\n\n[showing {MAX_ROWS} of {len(rows)}]"
              if len(rows) > MAX_ROWS else "")
    return f"{len(rows)} {what} in {where}:\n{body}{suffix}"


# --------------------------------------------------------------------------- #
# Files and folders
# --------------------------------------------------------------------------- #


@tool
def list_folder(folder: str, limit: int = 50) -> str:
    """List the files and folders inside a folder on this Mac.

    Use this whenever asked what is in a folder. Do not guess at contents.

    Args:
        folder: Folder to list, e.g. "Downloads", "~/Documents", "/tmp".
        limit: Maximum entries to return. Default 50.
    """
    def run() -> str:
        where = _safe_folder(folder)
        if not where.is_dir():
            return f"{where} does not exist or is not a folder."
        names = sorted(entry.name for entry in where.iterdir()
                       if not entry.name.startswith("."))
        return _describe_rows(names[:max(1, limit)], "items", where)

    return _guarded("bash", {"list_folder": folder}, run)


@tool
def count_files(folder: str, extension: str = "") -> str:
    """Count files in a folder, optionally only those with one extension.

    Use this for any "how many files..." question. Returns an exact number.

    Args:
        folder: Folder to count in, e.g. "Downloads".
        extension: Extension without the dot, e.g. "pdf". Omit to count all files.
    """
    def run() -> str:
        where = _safe_folder(folder)
        if not where.is_dir():
            return f"{where} does not exist or is not a folder."

        suffix = extension.strip().lstrip(".").lower()
        files = [entry for entry in where.iterdir()
                 if entry.is_file() and not entry.name.startswith(".")]
        if suffix:
            files = [f for f in files if f.suffix.lower() == f".{suffix}"]
            label = f"{suffix.upper()} files"
        else:
            label = "files"
        return f"There are exactly {len(files)} {label} in {where}."

    return _guarded("bash", {"count_files": folder, "extension": extension}, run)


@tool
def find_files(folder: str, name_contains: str, limit: int = 50) -> str:
    """Find files whose name contains some text, searching subfolders too.

    Args:
        folder: Folder to search under, e.g. "~/Documents".
        name_contains: Text to look for in file names, case-insensitive.
        limit: Maximum results. Default 50.
    """
    def run() -> str:
        where = _safe_folder(folder)
        if not where.is_dir():
            return f"{where} does not exist or is not a folder."

        needle = name_contains.strip().lower()
        if not needle:
            return "Give some text to search for in file names."

        matches = []
        for path in where.rglob("*"):
            if len(matches) >= max(1, limit):
                break
            if path.is_file() and needle in path.name.lower():
                matches.append(str(path.relative_to(where)))
        return _describe_rows(matches, f"files matching {name_contains!r}", where)

    return _guarded("bash", {"find_files": folder, "contains": name_contains}, run)


@tool
def read_file(path: str, max_lines: int = 100) -> str:
    """Read the beginning of a text file on this Mac.

    Args:
        path: File to read, e.g. "~/Desktop/notes.txt".
        max_lines: How many lines to read. Default 100.
    """
    def run() -> str:
        target = _safe_folder(path)
        if not target.is_file():
            return f"{target} does not exist or is not a file."
        try:
            with target.open("r", encoding="utf-8", errors="replace") as handle:
                lines = [next(handle, None) for _ in range(max(1, max_lines))]
        except OSError as exc:
            return f"Could not read {target}: {exc}"

        text = "".join(line for line in lines if line)
        if not text.strip():
            return f"{target} is empty."
        return f"{target}:\n{text}"

    return _guarded("bash", {"read_file": path}, run)


# --------------------------------------------------------------------------- #
# System and apps
# --------------------------------------------------------------------------- #

#: Fixed commands, so the model picks a key rather than writing a command.
#:
#: `wifi` is here as well as in `system_control` on purpose. "Is Wi-Fi
#: connected?" reads as a status lookup alongside hostname, disk and battery,
#: and the model routes it here every time — measured 0/3 for the alternative,
#: before *and* after a merge intended to fix it. Rather than argue with that a
#: third time, both paths now answer correctly. Duplication is cheaper than a
#: wrong answer (RELIABILITY.md).
_SYSTEM_FACTS = {
    "macos_version": "sw_vers -productVersion",
    "hostname": "scutil --get ComputerName",
    "disk_free": "df -h / | tail -1 | awk '{print $4\" free of \"$2}'",
    "battery": "pmset -g batt | grep -Eo '[0-9]+%' | head -1",
    "date": "date '+%A %d %B %Y, %H:%M'",
    "uptime": "uptime",
    "wifi": ("networksetup -getairportnetwork "
             "$(networksetup -listallhardwareports "
             "| awk '/Wi-Fi|AirPort/{getline; print $2}' | head -1)"),
}


@tool
def system_info(fact: str) -> str:
    """Look up a fact about this Mac.

    Args:
        fact: One of "macos_version", "hostname", "disk_free", "battery",
            "date", "uptime", "wifi".
    """
    def run() -> str:
        key = fact.strip().lower()
        command = _SYSTEM_FACTS.get(key)
        if command is None:
            return f"Unknown fact {fact!r}. Choose one of: {', '.join(_SYSTEM_FACTS)}."
        result = shell_tool.run(command, timeout=15)
        value = result.output.strip()
        return f"{key}: {value}" if result.ok and value else f"Could not read {key}."

    return _guarded("bash", {"system_info": fact}, run)


@tool
def open_app(name: str) -> str:
    """Open an application on this Mac.

    Args:
        name: Application name, e.g. "Safari", "Notes", "Pages".
    """
    def run() -> str:
        app = name.strip()
        if not app:
            return "Give an application name."
        result = shell_tool.run(f"open -a {shlex.quote(app)}", timeout=20)
        return f"Opened {app}." if result.ok else f"Could not open {app}: {result.output[:120]}"

    return _guarded("bash", {"open_app": name}, run)


#: Read-only app queries, as fixed scripts. Keeps the model out of AppleScript
#: authoring, which it is no better at than shell.
_APP_QUERIES = {
    "unread_mail": ('tell application "Mail" to return unread count of inbox',
                    "unread messages"),
    "todays_events": ('tell application "Calendar" to return summary of every event of '
                      'every calendar whose start date is greater than (current date) - 0',
                      "events today"),
    "notes_count": ('tell application "Notes" to return count of notes', "notes"),
    "reminders": ('tell application "Reminders" to return name of every reminder '
                  'of list 1 whose completed is false', "open reminders"),
}


@tool
def app_info(query: str) -> str:
    """Ask a Mac app for information.

    Args:
        query: One of "unread_mail", "todays_events", "notes_count", "reminders".
    """
    def run() -> str:
        key = query.strip().lower()
        entry = _APP_QUERIES.get(key)
        if entry is None:
            return f"Unknown query {query!r}. Choose one of: {', '.join(_APP_QUERIES)}."
        script, label = entry
        result = run_applescript(script)
        if not result.ok:
            return f"Could not get {label}: {result.for_model()[:160]}"
        value = result.output.strip()
        return f"{label}: {value}" if value else f"No {label}."

    return _guarded("applescript", {"app_info": query}, run)


#: Query primitives — read-only. The full on-device tool set is assembled in
#: `actions.py`, which imports this module; putting it here instead would make
#: the dependency circular.
QUERY_TOOLS = [
    count_files, list_folder, find_files, read_file,
    system_info, open_app,
]

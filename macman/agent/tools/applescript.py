"""Tier 2 — AppleScript / JXA.

Structured control of scriptable apps: Mail, Calendar, Notes, Reminders,
Finder, Pages, Numbers, Keynote, browser tabs. Deterministic where tier 3 and 4
are probabilistic, and — importantly — **most of it survives a locked screen**,
which is what makes headless mode useful rather than a consolation prize.

The exception is `System Events` UI scripting, which drives the interface and is
blocked when locked. That distinction is measured, not assumed: see
`tests/tasks/locked_boundary.py`.

Scripts are passed on stdin rather than via `-e`, which sidesteps a layer of
shell quoting for scripts containing quotes, newlines, or `$`.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass

DEFAULT_TIMEOUT_SECONDS = 30

#: Timeout for the *first* script sent to a given app.
#:
#: macOS gates cross-app Apple events behind a one-time Automation prompt, and
#: it blocks the sender **synchronously** while that prompt is unanswered —
#: there is no -1743 until the user clicks Deny. An unattended MACman would
#: otherwise stall for the full timeout on every first contact. Once an app has
#: answered successfully it is remembered and the normal timeout applies.
#:
#: `AEDeterminePermissionToAutomateTarget` would answer this without sending an
#: event, but PyObjC doesn't expose it, and reading TCC.db needs Full Disk Access.
FIRST_CONTACT_TIMEOUT_SECONDS = 3

MAX_OUTPUT_CHARS = 20_000

#: Apps this process has successfully scripted, so first contact is paid once.
_authorized: set[str] = set()

#: `tell application "X"` (AppleScript) and `Application("X")` (JXA).
_TARGET_RE = re.compile(r'(?:tell\s+application|Application\s*\()\s*"([^"]+)"', re.I)

#: AppleScript error numbers worth translating. Raw osascript errors are cryptic
#: and, in the permission case, describe a problem only the user can fix.
_ERROR_HINTS: dict[int, str] = {
    -1743: (
        "Not authorised to control this app. Grant Automation permission: "
        "System Settings → Privacy & Security → Automation."
    ),
    -600: "The target application isn't running. Launch it first (`open -a <App>`).",
    -1728: "The script referred to an object that doesn't exist.",
    -1712: "The Apple event timed out — the app was busy or showing a dialog.",
    -2741: "Syntax error: the script is malformed.",
    -2740: "Syntax error: the script is malformed.",
    -10004: "A privilege violation — the app refused the request.",
}

#: `System Events` UI scripting needs an unlocked screen; app scripting does not.
_UI_SCRIPTING = "system events"


@dataclass(frozen=True)
class AppleScriptResult:
    script: str
    ok: bool
    output: str
    error_number: int | None
    elapsed_ms: int

    def for_model(self) -> str:
        """Render for the model, leading with the outcome.

        On failure the translated hint comes first, because the actionable part
        of an AppleScript error is almost never its text.
        """
        if self.ok:
            return f"ok ({self.elapsed_ms} ms)\n{self.output or '(no result)'}"

        parts = [f"failed ({self.elapsed_ms} ms)"]
        if self.error_number is not None and self.error_number in _ERROR_HINTS:
            parts.append(_ERROR_HINTS[self.error_number])
        parts.append(self.output or "(no error text)")
        return "\n".join(parts)


def _parse_error_number(stderr: str) -> int | None:
    """Extract the `(-1743)`-style code osascript appends to failures."""
    marker = stderr.rfind("(-")
    if marker == -1:
        return None
    end = stderr.find(")", marker)
    if end == -1:
        return None
    try:
        return int(stderr[marker + 1:end])
    except ValueError:
        return None


def needs_unlocked_screen(script: str) -> bool:
    """Whether `script` drives the UI and therefore needs an unlocked screen.

    Used to fail loudly with a useful reason instead of letting the script run
    and silently do nothing behind a lock screen.
    """
    return _UI_SCRIPTING in script.lower()


def run(
    script: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    javascript: bool = False,
) -> AppleScriptResult:
    """Execute an AppleScript (or JXA) script.

    Args:
        script: Source, passed on stdin.
        timeout: Seconds before the script is killed.
        javascript: Run as JavaScript for Automation instead of AppleScript.

    Note:
        The first call targeting any given app raises a one-time Automation
        prompt. Until it is answered the call fails with -1743, which
        `for_model` translates into the fix.
    """
    command = ["/usr/bin/osascript"]
    if javascript:
        command += ["-l", "JavaScript"]
    command.append("-")  # read the script from stdin

    # Time-box first contact with an app so an unanswered Automation prompt
    # costs seconds rather than the whole timeout.
    targets = set(_TARGET_RE.findall(script))
    unseen = targets - _authorized
    effective_timeout = min(timeout, FIRST_CONTACT_TIMEOUT_SECONDS) if unseen else timeout

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            input=script,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )
        ok = completed.returncode == 0
        output = (completed.stdout if ok else completed.stderr).strip()
        error_number = None if ok else _parse_error_number(completed.stderr)
        if ok:
            # Reaching a result proves the events were permitted.
            _authorized.update(targets)
    except subprocess.TimeoutExpired:
        ok, error_number = False, None
        if unseen:
            apps = ", ".join(sorted(unseen))
            output = (
                f"Timed out waiting for Automation permission for: {apps}.\n"
                f"macOS is very likely showing an approval dialog on the Mac right now. "
                f"Approve it, or grant it under System Settings → Privacy & Security → "
                f"Automation, then retry."
            )
        else:
            output = f"Script exceeded the {timeout}s timeout."
    except OSError as exc:
        ok, output, error_number = False, f"Failed to launch osascript: {exc}", None

    return AppleScriptResult(
        script=script,
        ok=ok,
        output=output[:MAX_OUTPUT_CHARS],
        error_number=error_number,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


def tell(app: str, body: str, **kwargs) -> AppleScriptResult:
    """Convenience wrapper for the overwhelmingly common `tell application` form."""
    return run(f'tell application "{app}"\n{body}\nend tell', **kwargs)

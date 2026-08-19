"""The guarded tool set handed to the model.

Every tool passes through `_guarded` before it runs, which enforces three things
in this order:

1. **Tier** — the current lock state must permit this tool (DESIGN.md §6.3).
2. **Guard** — `guard.classify` may deny outright or require confirmation.
3. **Audit** — the intent is logged before execution, the result after.

The gate lives in this wrapper rather than in the Tool Runner loop on purpose.
Enforcement here is true by construction: it cannot be bypassed by a change to
the runner's iteration API, and it applies identically whichever engine or loop
is driving. A refusal is *returned* to the model rather than raised, so it can
read the reason and adapt instead of the session dying.
"""

from __future__ import annotations

import json
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable

from macman.agent.tools.schema import tool

from macman.agent import guard
from macman.agent.tools import applescript as applescript_tool
from macman.agent.tools import shell as shell_tool
from macman.agent.tools import ui as ui_tool
from macman.security import lockstate
from macman.security.audit import AuditLog


@dataclass
class ToolContext:
    """Per-session state the tools need but the model must not supply."""

    session_id: str
    engine: str
    audit: AuditLog
    #: Asks the owner to approve a CONFIRM action. Returns True to proceed.
    #: A terminal prompt in v0; spoken or texted once the channels exist.
    confirm: Callable[[str, str], bool]

    def refresh_tier(self) -> lockstate.LockState:
        """Re-read lock state; a Mac that locks mid-session must downgrade."""
        return lockstate.read()


_context: ContextVar[ToolContext | None] = ContextVar("macman_tool_context", default=None)


def set_context(context: ToolContext) -> None:
    _context.set(context)


def current_context() -> ToolContext | None:
    """The active session's context, or None when there is no owner to ask.

    Exposed for tools that need more than a yes/no — `claude_code` builds a
    full egress Disclosure and needs the audit log and session id to record
    it. Returning None rather than raising keeps the fail-closed contract:
    every caller must decide what "nobody is here" means for them, and for
    anything that sends data the answer is refuse.
    """
    return _context.get()


def require_confirmation(reason: str, summary: str) -> bool:
    """Ask the owner to approve an action, from inside a tool.

    `guard.classify` matches patterns in argument *text*, which works for a
    shell command but not for a typed primitive — `{"action": "trash"}` looks
    innocuous as a string. Destructive primitives therefore ask explicitly
    rather than hoping a regex catches them.

    Returns False when there is no session context, so an unattended call
    fails closed rather than proceeding unapproved.
    """
    context = _context.get()
    if context is None:
        return False
    return context.confirm(reason, summary)


def _guarded(tool: str, args: dict[str, Any], run: Callable[[], Any]) -> Any:
    """Apply tier, guard, and audit around one tool invocation.

    Returns whatever the tool produced. Usually a string, but the SDK also
    accepts a list of content blocks, which is how `screenshot` returns an
    image the model can actually see rather than a description of one.
    """
    context = _context.get()
    if context is None:
        return "Refused: no active MACman session context."

    state = context.refresh_tier()
    if not state.allows(tool):
        context.audit.security(event="tier_refusal", tool=tool, tier=state.tier.value)
        return f"Refused: `{tool}` is unavailable right now. {state.explain()}"

    decision = guard.classify(tool, args)

    if decision.verdict is guard.Verdict.DENY:
        context.audit.security(
            event="denied", tool=tool, args=args, reason=decision.reason
        )
        return (
            f"Refused: this {decision.reason}. Denied in policy — confirmation "
            f"cannot override it. Tell the owner rather than trying another route."
        )

    if decision.verdict is guard.Verdict.CONFIRM:
        summary = json.dumps(args, default=str)[:300]
        if not context.confirm(decision.reason, summary):
            context.audit.security(
                event="confirmation_declined", tool=tool, args=args,
                reason=decision.reason,
            )
            return f"Refused by the owner: this {decision.reason}."

    context.audit.tool_call(
        session_id=context.session_id, engine=context.engine, tool=tool,
        args=args, verdict=decision.verdict.value, tier=state.tier.value,
    )

    started = time.monotonic()
    try:
        result = run()
        ok = True
    except Exception as exc:  # surfaced to the model, not fatal to the session
        result, ok = f"Tool failed: {exc}", False

    context.audit.tool_result(
        session_id=context.session_id, tool=tool, ok=ok, result=result,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
    return result if isinstance(result, (str, list)) else str(result)


# --------------------------------------------------------------------------- #
# Tools. Docstrings become the schema the model sees, so they are written for
# the model rather than for a reader of this file.
# --------------------------------------------------------------------------- #


@tool
def bash(command: str, timeout: int = 60) -> str:
    """Run a shell command on the Mac with zsh and return its combined output.

    The widest-coverage tool available and the first one to reach for. Use it for
    file operations, git, launching apps with `open -a`, reading and writing
    files, running scripts, and querying system state. Works even when the screen
    is locked.

    Args:
        command: The shell command line to run.
        timeout: Seconds to allow before the command is killed. Default 60.
    """
    return _guarded(
        "bash", {"command": command},
        lambda: shell_tool.run(command, timeout=timeout).for_model(),
    )


@tool
def applescript(script: str, javascript: bool = False) -> str:
    """Run an AppleScript (or JavaScript for Automation) script.

    Use for structured control of scriptable apps — Mail, Calendar, Notes,
    Reminders, Finder, Pages, Numbers, Keynote, Safari and Chrome tabs. Much more
    reliable than driving those apps through their interface. Mostly works while
    the screen is locked, except for `System Events` UI scripting.

    The first script sent to any given app may need a one-time permission
    approval on the Mac; you will be told if that is what happened.

    Args:
        script: The script source.
        javascript: Run as JavaScript for Automation instead of AppleScript.
    """
    return _guarded(
        "applescript", {"script": script},
        lambda: applescript_tool.run(script, javascript=javascript).for_model(),
    )


@tool
def ui_query(app: str, interactive_only: bool = True, max_depth: int = 8) -> str:
    """Read an app's Accessibility tree as JSON, so you can see its interface as text.

    Every node carries a `path` you can pass to `ui_press` or `ui_set_value`.
    Prefer `ui_find` when you already know what you are looking for — a full tree
    is large. The app must already be running.

    Args:
        app: Application name or bundle identifier, e.g. "Finder".
        interactive_only: Prune nodes that are neither actionable nor on a path
            to something actionable. Usually what you want.
        max_depth: How deep to walk. Deeper trees cost more.
    """
    return _guarded(
        "ui_query", {"app": app},
        lambda: json.dumps(
            ui_tool.query(app, max_depth=max_depth, interactive_only=interactive_only),
            indent=1,
        ),
    )


@tool
def ui_find(app: str, role: str = "", label: str = "") -> str:
    """Find elements in an app's interface by role and/or label text.

    The efficient way to locate a control: "the Share Screen button" resolves to
    a path without pulling the whole tree into view. Returns matching elements
    with their paths.

    Args:
        app: Application name or bundle identifier.
        role: Accessibility role to match exactly, e.g. "AXButton",
            "AXTextField", "AXMenuItem". Omit to match any role.
        label: Substring to match against an element's label, case-insensitive.
            Omit to match any label.
    """
    def run() -> str:
        found = ui_tool.find(app, role=role or None, label=label or None)
        if not found:
            return f"No elements in {app} matched role={role!r} label={label!r}."
        return json.dumps(
            [{"path": e.path, "role": e.role, "label": e.label, "enabled": e.enabled}
             for e in found[:40]],
            indent=1,
        )

    return _guarded("ui_query", {"app": app, "role": role, "label": label}, run)


@tool
def ui_press(app: str, path: str) -> str:
    """Press a UI element identified by the path from `ui_find` or `ui_query`.

    Presses buttons, menu items, checkboxes and links by their position in the
    Accessibility tree, not by screen coordinates — so it is unaffected by window
    position, resolution, or theme. Requires an unlocked screen.

    Args:
        app: Application name or bundle identifier.
        path: The element's `path`, e.g. "0/2/1".
    """
    return _guarded("ui_click", {"app": app, "path": path},
                    lambda: ui_tool.press(app, path))


@tool
def ui_set_value(app: str, path: str, value: str) -> str:
    """Set a UI element's value directly, without synthesising keystrokes.

    The reliable way to fill a text field: it cannot drop characters or land in
    the wrong field the way typing can. Requires an unlocked screen.

    Args:
        app: Application name or bundle identifier.
        path: The element's `path` from `ui_find` or `ui_query`.
        value: The text to place in the element.
    """
    return _guarded("ui_click", {"app": app, "path": path, "value": value},
                    lambda: ui_tool.set_value(app, path, value))


#: Handed to the Tool Runner. Order is the order the model sees them, which
#: reinforces the tiering described in the system prompt.
ALL_TOOLS = [bash, applescript, ui_find, ui_query, ui_press, ui_set_value]


def tool_by_name(name: str):
    """Look up a tool the model asked for by name.

    Used by the local engine, which receives tool requests proxied back from
    the Swift helper and has to resolve them against this same registry — so
    an on-device tool call goes through exactly the guard the cloud engine's
    calls do.
    """
    return next((tool for tool in ALL_TOOLS if tool.to_dict()["name"] == name), None)

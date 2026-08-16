"""Session lock state and the capability tier it implies.

MACman never bypasses the screen lock (DESIGN.md §6.1). Instead it detects the
lock state and narrows what it will attempt, announcing the result rather than
silently failing halfway through a task.

State is cheap to read, so callers should re-read it each turn: a Mac that locks
mid-session must downgrade cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import Quartz

# CoreGraphics session dictionary keys. `CGSSessionScreenIsLocked` is *absent*
# rather than false when unlocked, so a missing key means unlocked.
_ON_CONSOLE = "kCGSSessionOnConsoleKey"
_SCREEN_LOCKED = "CGSSessionScreenIsLocked"
_USER_NAME = "kCGSSessionUserNameKey"


class Tier(str, Enum):
    """Capability available right now. Mirrors the table in DESIGN.md §6.3."""

    #: Screen unlocked — every tool tier, including UI automation and screenshots.
    FULL = "full"
    #: Screen locked — shell, files, and most AppleScript. No UI automation.
    HEADLESS = "headless"
    #: No usable session on the console. MACman can do nothing.
    UNAVAILABLE = "unavailable"


#: Tool tiers each capability tier permits. Tiers 1-2 survive a locked screen;
#: 3-5 need the window server in an unlocked state.
_TIER_TOOLS: dict[Tier, frozenset[str]] = {
    Tier.FULL: frozenset({"bash", "applescript", "ui_query", "ui_click",
                          "screenshot", "computer", "shortcuts"}),
    Tier.HEADLESS: frozenset({"bash", "applescript"}),
    Tier.UNAVAILABLE: frozenset(),
}


@dataclass(frozen=True)
class LockState:
    on_console: bool
    screen_locked: bool
    user_name: str | None
    tier: Tier

    def allows(self, tool: str) -> bool:
        """Whether `tool` can run under the current tier."""
        return tool in _TIER_TOOLS[self.tier]

    def explain(self) -> str:
        """A sentence for the user, spoken on a call or prefixed to a reply.

        Announcing the tier up front is what keeps 'why did nothing happen?'
        from being a failure mode.
        """
        if self.tier is Tier.FULL:
            return "Mac is unlocked — full access."
        if self.tier is Tier.HEADLESS:
            return (
                "Mac is locked, so I can work with files, the shell, and scriptable "
                "apps, but I can't drive the interface or show you the screen."
            )
        return "No active session on this Mac — I can't do anything until someone logs in."


def read() -> LockState:
    """Read the current console session.

    A missing session dictionary means no console session at all, which we treat
    as locked-and-unusable rather than guessing.
    """
    session = Quartz.CGSessionCopyCurrentDictionary()
    if session is None:
        return LockState(False, True, None, Tier.UNAVAILABLE)

    on_console = bool(session.get(_ON_CONSOLE, False))
    screen_locked = bool(session.get(_SCREEN_LOCKED, False))

    if not on_console:
        tier = Tier.UNAVAILABLE
    elif screen_locked:
        tier = Tier.HEADLESS
    else:
        tier = Tier.FULL

    return LockState(
        on_console=on_console,
        screen_locked=screen_locked,
        user_name=session.get(_USER_NAME),
        tier=tier,
    )

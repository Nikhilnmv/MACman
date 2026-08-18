"""Dangerous-action gate.

Every tool call is classified here before it executes, via a Tool Runner
per-turn hook (DESIGN.md §6.4). Three outcomes:

* ``ALLOW``   — run it.
* ``CONFIRM`` — the agent must ask out loud and get an explicit yes first.
* ``DENY``    — refused in code; no confirmation can override it.

**What this is and isn't.** ``DENY`` is a real boundary: credential paths are
blocked here so that a successful prompt injection still cannot exfiltrate a
key. ``CONFIRM`` is defence in depth, not a sandbox — a determined attacker with
shell access can obfuscate around any pattern list. It exists to catch a
*mistaken* agent, not an adversarial one. The real containment is that MACman
runs as your user, cannot escalate to root, and cannot unlock the screen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from macman import config


class Verdict(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    #: Shown to the user when confirming, and logged in every case.
    reason: str

    @property
    def blocked(self) -> bool:
        return self.verdict is not Verdict.ALLOW


# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #

#: Hard denials. Not overridable by confirmation, because the whole point is to
#: survive an agent that has been convinced to ask nicely.
_DENY: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsecurity\s+(find-|dump-|export)", re.I),
     "reads the macOS Keychain"),
    (re.compile(r"ANTHROPIC_API_KEY|OPENAI_API_KEY|AWS_SECRET", re.I),
     "touches an API credential"),
)

#: Requires explicit user confirmation. Ordered most-specific first so the
#: reported reason is the useful one.
_CONFIRM: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+", re.I),
     "recursively deletes files"),
    (re.compile(r"\b(sudo|doas)\b", re.I),
     "runs with administrator privileges"),
    (re.compile(r"\b(diskutil|dd|mkfs|fdisk)\b", re.I),
     "operates on a disk directly"),
    (re.compile(r"\bcurl\b[^|;]*\|\s*(ba)?sh", re.I),
     "pipes a downloaded script straight into a shell"),
    (re.compile(r"\bgit\s+push\b.*(--force|-f)\b", re.I),
     "force-pushes, which can destroy remote history"),
    (re.compile(r"\bdefaults\s+delete\b", re.I),
     "deletes application preferences"),
    (re.compile(r"\b(shutdown|reboot|halt)\b", re.I),
     "shuts down or restarts the Mac"),
    (re.compile(r"\bpurchase|checkout|payment|\bbuy\b", re.I),
     "looks like a purchase"),
    (re.compile(r"tell\s+application\s+\"(Mail|Messages)\".*\b(send|deliver)\b", re.I | re.S),
     "sends a message or email on your behalf"),
    (re.compile(r"\bosascript\b.*\bkeystroke\b", re.I),
     "synthesises keystrokes"),
)


def _denied_path_touched(text: str) -> str | None:
    """Whether a command references a credential directory.

    Matches on the literal path *and* on the `~`-relative form, since a command
    string is checked before any shell expansion happens.

    Compared **case-folded**: macOS filesystems are case-insensitive by
    default, so `~/.SSH/id_ed25519` opens the same file as `~/.ssh/…`. An
    audit confirmed a case-sensitive check let that through and leaked real
    key material, so spelling must not be what decides this.
    """
    folded_text = text.casefold()
    for denied in config.DENIED_READ_PATHS:
        candidates = [str(denied)]
        try:
            candidates.append("~/" + str(denied.relative_to(Path.home())))
        except ValueError:
            pass
        for candidate in candidates:
            if candidate.casefold() in folded_text:
                return candidate
    return None


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def classify(tool: str, args: dict[str, Any]) -> Decision:
    """Decide whether a tool call may run.

    Args:
        tool: Tool name, e.g. ``"bash"`` or ``"applescript"``.
        args: The tool's arguments as the model supplied them.
    """
    # Read-only tools carry no destructive capability of their own.
    if tool in {"ui_query", "screenshot", "lock_state"}:
        return Decision(Verdict.ALLOW, "read-only")

    # Everything else is judged on its full argument text: a dangerous string
    # is dangerous whichever parameter it arrived in.
    text = " ".join(str(value) for value in args.values())

    if (path := _denied_path_touched(text)) is not None:
        return Decision(Verdict.DENY, f"references a protected credential path ({path})")

    for pattern, reason in _DENY:
        if pattern.search(text):
            return Decision(Verdict.DENY, reason)

    for pattern, reason in _CONFIRM:
        if pattern.search(text):
            return Decision(Verdict.CONFIRM, reason)

    return Decision(Verdict.ALLOW, "no dangerous pattern matched")

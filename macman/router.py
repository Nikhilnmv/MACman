"""Engine routing — decides local vs cloud for a task.

The defining constraint (DESIGN.md §3): **this module never makes a network
call.** Asking a cloud model "is this private?" has already leaked the filename
and the context. Routing is therefore decided by deterministic rules, with an
on-device model as the only fallback.

Two principles shape the rules:

* **Private is sticky.** Any private signal wins, even when developer signals
  are also present — "copy the figures from my spreadsheet into VS Code" is a
  private task. A false positive costs capability; a false negative costs a
  privacy breach, and those are not comparable.
* **There is no positive cloud signal.** Rules can only push *toward* local.
  Cloud is what remains when nothing suggests the task is personal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from macman.config import (
    DEFAULT_ENGINE,
    DEVELOPER_APPS,
    PRIVATE_APPS,
    PRIVATE_PATHS,
    Engine,
)

# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #

#: App names that are also ordinary English words. Matching these
#: case-insensitively would send "fix the numbers in this script" to the local
#: engine, so they must appear capitalised as a proper noun would be.
_AMBIGUOUS_APPS = frozenset({
    "Pages", "Numbers", "Notes", "Mail", "Contacts", "Calendar",
    "Reminders", "Preview", "Photos", "Messages", "Books", "Freeform", "Code",
})

#: Nouns that are inherently personal — no qualifier needed.
#: One-directional: these only ever route local.
_PRIVATE_NOUNS = re.compile(
    r"\b(?:tax(?:es)?|invoice|receipt|contract|lease|resume|cv|passport|"
    r"medical|prescription|diagnosis|salary|payslip|bank statement|mortgage|"
    r"insurance|testament|diary|journal|meeting notes|personal)\b",
    re.I,
)

#: Generic content nouns that signal privacy only when claimed as the user's
#: own. The possessive is the disambiguator, and it matters: "document this
#: function" is a coding task, "my document" is not. Without this distinction
#: "copy the figures from my spreadsheet into VS Code" routes to the cloud,
#: which is precisely the failure the stickiness rule exists to prevent.
_POSSESSIVE_PRIVATE = re.compile(
    r"\b(?:my|our)\s+(?:\w+\s+){0,2}?"
    r"(?:doc|document|documents|spreadsheet|presentation|deck|note|notes|"
    r"email|emails|inbox|message|messages|photo|photos|calendar|contact|"
    r"contacts|file|files|folder|resume|records?)\b",
    re.I,
)

#: Absolute or `~`-relative paths, quoted or bare.
_PATH_RE = re.compile(r"""["']([~/][^"']+)["']|(?<![\w"'])([~/][\w.\-/]+)""")


@dataclass(frozen=True)
class Route:
    engine: Engine
    #: Which rule fired, for the audit log.
    rule: str
    #: The specific token that triggered it, for the spoken announcement.
    evidence: str | None

    def announce(self) -> str:
        """One line telling the user which engine is about to run and why.

        Spoken at session start and on every switch. You should never be unsure
        whether a document just left your Mac.
        """
        if self.engine is Engine.LOCAL:
            where = "on-device"
            if self.evidence:
                return f"Running {where} — {self.evidence} is private, so nothing leaves this Mac."
            return f"Running {where} by default — nothing leaves this Mac."
        return f"Using Claude for this — {self.evidence or 'no private content detected'}."


# --------------------------------------------------------------------------- #
# Rules, in precedence order
# --------------------------------------------------------------------------- #


def _private_path_in(text: str) -> str | None:
    """First referenced path that lives under a private root.

    Path rules outrank app rules: a spreadsheet is private wherever it is opened.
    """
    for quoted, bare in _PATH_RE.findall(text):
        raw = quoted or bare
        try:
            candidate = Path(raw).expanduser()
        except (ValueError, RuntimeError):
            continue
        for private_root in PRIVATE_PATHS:
            if candidate == private_root or candidate.is_relative_to(private_root):
                return raw
    return None


def _app_named_in(text: str, apps: frozenset[str]) -> str | None:
    """First app from `apps` named in `text`.

    Ambiguous names must match case-sensitively; unambiguous ones need not.
    """
    for app in sorted(apps, key=len, reverse=True):
        flags = 0 if app in _AMBIGUOUS_APPS else re.I
        if re.search(rf"(?<!\w){re.escape(app)}(?!\w)", text, flags):
            return app
    return None


def _classify_on_device(text: str) -> Engine | None:
    """Fallback classifier using Apple's on-device model.

    Not yet wired up — it needs the `macman-local` Swift helper, which is
    blocked on the toolchain. Returning None falls through to DEFAULT_ENGINE,
    which is local, so the safe behaviour holds in the meantime.
    """
    return None


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def route(task: str, *, frontmost_app: str | None = None) -> Route:
    """Choose an engine for `task`.

    Args:
        task: The user's request, verbatim.
        frontmost_app: Active app, used only when the task names none itself.

    Returns:
        A `Route` carrying the engine, the rule that decided it, and the
        evidence — all three go to the audit log, and the evidence is spoken.
    """
    if (path := _private_path_in(task)) is not None:
        return Route(Engine.LOCAL, "path", path)

    if (app := _app_named_in(task, PRIVATE_APPS)) is not None:
        return Route(Engine.LOCAL, "app", app)

    for pattern in (_PRIVATE_NOUNS, _POSSESSIVE_PRIVATE):
        if (match := pattern.search(task)) is not None:
            return Route(Engine.LOCAL, "keyword", match.group(0).strip())

    if (app := _app_named_in(task, DEVELOPER_APPS)) is not None:
        return Route(Engine.CLOUD, "app", app)

    # Only consult the frontmost app once the task itself has offered nothing.
    if frontmost_app:
        if frontmost_app in PRIVATE_APPS:
            return Route(Engine.LOCAL, "frontmost", frontmost_app)
        if frontmost_app in DEVELOPER_APPS:
            return Route(Engine.CLOUD, "frontmost", frontmost_app)

    if (engine := _classify_on_device(task)) is not None:
        return Route(engine, "classifier", None)

    return Route(DEFAULT_ENGINE, "default", None)

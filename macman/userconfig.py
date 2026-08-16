"""User settings, stored outside the source tree.

Anything personal — your handle, your wake phrase, your model choice — lives in
``~/Library/Application Support/MACman/config.toml``, not in ``config.py``.

Two reasons, and the second is the one that matters:

* Nobody should have to edit Python to use a tool. Asking a user to open a
  source file and add their phone number loses most of them at the door.
* Personal data must not live in a repository. A hardcoded phone number in
  ``config.py`` is one ``git push`` away from being public.

Defaults live in code; the TOML only ever *overrides*. A missing or malformed
config therefore degrades to safe defaults rather than failing to start —
except for ``allowed_handles``, which defaults to empty, because the safe
default for "who may command my Mac" is nobody.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

STATE_DIR = Path(
    os.environ.get("MACMAN_STATE_DIR", Path.home() / "Library/Application Support/MACman")
)
CONFIG_PATH = STATE_DIR / "config.toml"

#: Every user-editable setting, with the value used when the file says nothing.
DEFAULTS: dict[str, Any] = {
    "allowed_handles": [],
    "wake_phrases": ["activate macman", "macman wake up", "hey macman"],
    "session_idle_minutes": 30,
    "wake_timeout_seconds": 120,
    "attach_screenshot": True,
}

_TEMPLATE = """\
# MACman settings.
# Edit by hand, or re-run: macman setup

# Handles permitted to command this Mac. Empty means nobody — messages from
# anyone not listed here are dropped before any model sees them.
# Format is E.164, exactly as Messages stores it: "+<country code><number>"
allowed_handles = {allowed_handles}

# Say one of these to open the door before authenticating. Matched
# case-insensitively anywhere in the message, so "Daddy's home, MACman wake up!"
# matches "macman wake up".
wake_phrases = {wake_phrases}

# How long a session survives without activity.
session_idle_minutes = {session_idle_minutes}

# How long after the wake phrase a code is accepted.
wake_timeout_seconds = {wake_timeout_seconds}

# Attach a screenshot to replies when the screen is unlocked.
attach_screenshot = {attach_screenshot}
"""


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        inner = ", ".join(f'"{item}"' for item in sorted(value))
        return f"[{inner}]"
    return f'"{value}"'


def load() -> dict[str, Any]:
    """Read settings, falling back to defaults for anything absent.

    Never raises. A corrupt config should leave MACman running on defaults
    with nobody allowlisted, not refusing to start — the failure mode of a
    typo shouldn't be an unusable tool.
    """
    settings = dict(DEFAULTS)
    if not CONFIG_PATH.exists():
        return settings

    try:
        with CONFIG_PATH.open("rb") as handle:
            settings.update(tomllib.load(handle))
    except (OSError, tomllib.TOMLDecodeError):
        return dict(DEFAULTS)

    return settings


def save(settings: dict[str, Any]) -> Path:
    """Write settings, preserving the explanatory comments.

    Written 0600: it names who may command this Mac, which is not something to
    leave world-readable on a shared machine.
    """
    merged = {**DEFAULTS, **settings}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        _TEMPLATE.format(**{key: _toml_value(merged[key]) for key in DEFAULTS}),
        encoding="utf-8",
    )
    CONFIG_PATH.chmod(0o600)
    return CONFIG_PATH


def update(**changes: Any) -> Path:
    """Change individual settings, leaving the rest untouched."""
    settings = load()
    settings.update(changes)
    return save(settings)


def is_configured() -> bool:
    """Whether anyone is permitted to reach MACman at all."""
    return bool(load()["allowed_handles"])

"""Configuration for MACman.

Split by who owns each setting:

* **Here, in code** — the routing rules (``PRIVATE_APPS``, ``PRIVATE_PATHS``)
  and the security constants (``DENIED_READ_PATHS``, ``SCRUBBED_ENV_VARS``,
  lockout thresholds). These define what "private" means and what MACman
  refuses to do; they are part of the design, not preferences to tune.
* **In ``userconfig``** — anything personal or per-install: your handle, your
  wake phrase, your model choice. Read from a TOML file outside the source
  tree so nobody has to edit Python, and so no phone number ever lands in a
  repository.

See DESIGN.md §3 (routing) and §6 (security).
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from macman import userconfig

#: User-editable settings, read once at import. Everything personal lives here
#: rather than below — see `userconfig` for why.
_user = userconfig.load()

# --------------------------------------------------------------------------- #
# Engines
# --------------------------------------------------------------------------- #


class Engine(str, Enum):
    """Which model handles a task.

    ``LOCAL`` never leaves the Mac. ``CLOUD`` reaches the Anthropic API.
    """

    LOCAL = "local"
    CLOUD = "cloud"


#: Anthropic model for the developer task set. Thinking is on by default here.
CLOUD_MODEL = "claude-opus-5"

#: Fast conversational model for call narration, so the work loop isn't in the
#: latency path of "what are you doing right now?".
VOICE_MODEL = "claude-haiku-4-5"

#: Effort for the work loop. `high` is the API default; `medium` is the main
#: lever if cost runs hot, since output tokens dominate spend.
CLOUD_EFFORT = "high"



# --------------------------------------------------------------------------- #
# Routing — see DESIGN.md §3
# --------------------------------------------------------------------------- #

#: Apps whose content is personal. Anything touching these is pinned to the
#: local engine, regardless of what the task looks like.
PRIVATE_APPS: frozenset[str] = frozenset({
    "Pages", "Numbers", "Keynote",
    "Notes", "Mail", "Contacts", "Calendar", "Reminders",
    "Preview", "Finder", "Photos", "Messages", "FaceTime",
    "Freeform", "Stickies", "Books",
})

#: Apps where the developer task set applies. Code sent to Claude here is code
#: you would already be pasting into Claude by hand.
DEVELOPER_APPS: frozenset[str] = frozenset({
    "Code", "Visual Studio Code", "Cursor", "Xcode", "Zed", "Sublime Text",
    "Terminal", "iTerm2", "Warp", "Ghostty",
    "Safari", "Google Chrome", "Arc", "Firefox",
    "Docker Desktop", "Postman", "TablePlus",
})

#: Paths whose contents are private no matter which app opened them. Path rules
#: outrank app rules — a spreadsheet opened in VS Code is still private.
PRIVATE_PATHS: tuple[Path, ...] = (
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Library/Mobile Documents",  # iCloud Drive
)

#: Unknown apps route here. Local is the safe default: the cost of wrongly
#: keeping something on-device is reduced capability, not a privacy breach.
DEFAULT_ENGINE = Engine.LOCAL


# --------------------------------------------------------------------------- #
# Access control — see DESIGN.md §6
# --------------------------------------------------------------------------- #

#: iMessage handles permitted to reach MACman. Everything else is logged and
#: dropped before any engine sees it.
#:
#: Sourced from the user's config file, never hardcoded here — a phone number
#: in a source file is one `git push` from being public. Empty by default:
#: the safe answer to "who may command my Mac" is nobody.
ALLOWED_HANDLES: frozenset[str] = frozenset(_user["allowed_handles"])

#: Phrases that open the door to authentication. Matched case-insensitively as
#: a substring, so "Daddy's home, MACman wake up!" still hits "macman wake up".
#:
#: Before this existed, any text from an allowlisted number that wasn't a valid
#: code was fed straight to the TOTP verifier and counted as a failed attempt —
#: a stray "hey" could contribute toward a lockout. Requiring a wake phrase
#: first means idle chatter is ignored, not penalised.
WAKE_PHRASES: frozenset[str] = frozenset(
    phrase.lower() for phrase in _user["wake_phrases"]
)

#: How long after the wake phrase MACman will accept a code, before it goes
#: back to ignoring everything from that handle.
WAKE_TIMEOUT_SECONDS = _user["wake_timeout_seconds"]

#: How long an authenticated session stays valid without activity.
SESSION_IDLE_TIMEOUT_SECONDS = _user["session_idle_minutes"] * 60

#: Failed TOTP attempts before MACman locks out and alerts you.
MAX_AUTH_FAILURES = 5
AUTH_LOCKOUT_SECONDS = 15 * 60

#: Keychain service name holding the TOTP shared secret. Never in a file.
KEYCHAIN_SERVICE = "com.macman.totp"

#: Paths the shell tool refuses to read, enforced in code rather than by
#: prompting — a prompt-injection bypass must not be sufficient (DESIGN.md §6.4).
DENIED_READ_PATHS: tuple[Path, ...] = (
    Path.home() / ".ssh",
    Path.home() / ".aws",
    Path.home() / ".gnupg",
    Path.home() / "Library/Keychains",
)

#: Environment variables scrubbed from every shell tool invocation.
SCRUBBED_ENV_VARS: frozenset[str] = frozenset({
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN", "GITHUB_TOKEN", "GH_TOKEN",
})


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

STATE_DIR = userconfig.STATE_DIR
AUDIT_LOG = STATE_DIR / "audit.jsonl"
HELPERS_BIN = Path(__file__).resolve().parent.parent / "helpers" / ".build" / "release"

"""Reading and changing settings on behalf of MACman.app.

The app draws the settings window; this decides what a setting *is* and whether
a change is allowed. Keeping validation here rather than in Swift means one
implementation governs the CLI, the app, and anyone editing `config.toml` by
hand — three front ends that must not disagree about what a valid allowlist is.

## Secrets never travel back

`read()` reports whether a credential exists, never its value. Both the TOTP
secret and the Claude API key live in the macOS Keychain, and once written they
are not readable by any UI here — including this one. A settings pane that can
display your API key is a settings pane that can leak it.

## Why the daemon writes the file

The app could write `config.toml` itself. It does not, because the daemon
already owns the file's format, its 0600 permissions and its validation, and a
second writer is how two processes end up disagreeing about what is on disk.
The app asks; this validates, writes, and says what happened.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import keyring

from macman import config, userconfig
from macman.security import auth, egress, permissions

#: Keychain entry for the Claude key. Separate service from the TOTP secret so
#: revoking one cannot disturb the other.
CLOUD_KEYCHAIN_SERVICE = "com.macman.cloud"
_CLOUD_ACCOUNT = "anthropic-api-key"

#: An iMessage handle: E.164 phone number, or an Apple ID email address.
_HANDLE = re.compile(r"^(\+\d{7,15}|[^@\s]+@[^@\s]+\.[^@\s]+)$")


class SettingRejected(ValueError):
    """Raised when a proposed change is not allowed."""


# --------------------------------------------------------------------------- #
# The Claude key
# --------------------------------------------------------------------------- #


def cloud_key() -> str | None:
    """The Claude API key, Keychain first.

    The environment is still honoured so an existing `.env` keeps working, but
    the Keychain is preferred: a key in a dotfile is readable by anything
    running as you, and survives in shell history and backups.
    """
    import os

    stored = keyring.get_password(CLOUD_KEYCHAIN_SERVICE, _CLOUD_ACCOUNT)
    if stored:
        return stored
    return os.environ.get("ANTHROPIC_API_KEY") or None


def set_cloud_key(key: str) -> None:
    """Store a Claude key in the Keychain.

    Shape is checked, not validity — confirming a key really works costs an API
    call, and silently spending someone's money to validate a text field is not
    a reasonable thing to do without asking.
    """
    cleaned = key.strip()
    if not cleaned:
        raise SettingRejected("No key given.")
    if not cleaned.startswith("sk-ant-"):
        raise SettingRejected(
            "That doesn't look like an Anthropic key — they start with "
            "'sk-ant-'. Nothing was saved."
        )
    keyring.set_password(CLOUD_KEYCHAIN_SERVICE, _CLOUD_ACCOUNT, cleaned)


def clear_cloud_key() -> None:
    try:
        keyring.delete_password(CLOUD_KEYCHAIN_SERVICE, _CLOUD_ACCOUNT)
    except keyring.errors.PasswordDeleteError:
        pass                                   # already absent is success


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def read() -> dict[str, Any]:
    """Everything the settings window needs, and nothing secret."""
    settings = userconfig.load()

    return {
        "permissions": [
            {
                "key": permission.key,
                "name": permission.name,
                "granted": permission.granted(),
                "because": permission.because,
                "unlocks": [c.name for c in permissions.unlocks(permission.key)],
            }
            for permission in permissions.PERMISSIONS.values()
        ],
        "capabilities": [
            {
                "name": capability.name,
                "available": capability.available(),
                "without": capability.without,
                "missing": [p.name for p in capability.missing()],
            }
            for capability in permissions.CAPABILITIES
        ],
        "allowed_handles": list(settings.get("allowed_handles", [])),
        "wake_phrases": list(settings.get("wake_phrases", [])),
        "session_idle_minutes": int(settings.get("session_idle_minutes", 30)),
        "wake_timeout_seconds": int(settings.get("wake_timeout_seconds", 120)),
        "attach_screenshot": bool(settings.get("attach_screenshot", True)),
        # Presence only. Never the value, for either credential.
        "totp_configured": auth.is_configured(),
        "cloud_key_configured": cloud_key() is not None,
        "pre_approvals": [
            {
                "category": rule.category,
                "path": str(rule.path_prefix),
                "expires_at": rule.expires_at,
                "describe": rule.describe(),
            }
            for rule in egress.load_pre_approvals()
        ],
        "config_path": str(userconfig.CONFIG_PATH),
        "audit_path": str(config.AUDIT_LOG),
    }


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def _validate_handles(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise SettingRejected("The allowlist must be a list.")
    cleaned: list[str] = []
    for entry in value:
        handle = str(entry).strip()
        if not handle:
            continue
        if not _HANDLE.match(handle):
            raise SettingRejected(
                f"{handle!r} isn't a usable handle. Use a phone number in "
                "international form like +447700900123, or an Apple ID email."
            )
        cleaned.append(handle)
    # Duplicates are harmless but make the list confusing to read back.
    return list(dict.fromkeys(cleaned))


def _validate_phrases(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise SettingRejected("Wake phrases must be a list.")
    cleaned = [str(entry).strip() for entry in value if str(entry).strip()]
    if not cleaned:
        raise SettingRejected(
            "At least one wake phrase is needed, or nothing can start a session."
        )
    if any(len(phrase) < 4 for phrase in cleaned):
        raise SettingRejected(
            "Wake phrases must be at least 4 characters — a short one matches "
            "ordinary conversation and would wake MACman constantly."
        )
    return cleaned


def _validate_int(value: Any, *, low: int, high: int, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SettingRejected(f"{label} must be a number.") from None
    if not low <= number <= high:
        raise SettingRejected(f"{label} must be between {low} and {high}.")
    return number


#: field → validator. A field absent from here cannot be set at all, so the app
#: cannot write arbitrary keys into the config by sending an unexpected name.
_VALIDATORS = {
    "allowed_handles": _validate_handles,
    "wake_phrases": _validate_phrases,
    "session_idle_minutes": lambda v: _validate_int(
        v, low=1, high=1440, label="Session timeout"),
    "wake_timeout_seconds": lambda v: _validate_int(
        v, low=10, high=3600, label="Wake timeout"),
    "attach_screenshot": lambda v: bool(v),
}


def set_field(field: str, value: Any) -> Any:
    """Validate and persist one setting. Returns the stored value.

    Raises:
        SettingRejected: with a message written for the person who typed it,
            not for a log.
    """
    validator = _VALIDATORS.get(field)
    if validator is None:
        raise SettingRejected(f"{field!r} is not a setting that can be changed here.")

    cleaned = validator(value)
    userconfig.update(**{field: cleaned})
    return cleaned


def add_pre_approval(category: str, path: str, days: int) -> str:
    """Grant standing permission for one narrow kind of cloud task.

    Deliberately awkward to make broad: a category and a real directory, with
    an expiry capped at 90 days. A rule covering the home directory would be a
    blanket allow wearing a scope, so it is refused.
    """
    import time

    folder = Path(path).expanduser()
    if not folder.is_dir():
        raise SettingRejected(f"{folder} isn't a folder on this Mac.")
    if folder == Path.home():
        raise SettingRejected(
            "Your home folder is too broad to pre-approve — that would cover "
            "everything and defeat asking. Choose a project folder."
        )
    if not str(category).strip():
        raise SettingRejected("A pre-approval needs a category.")

    span = _validate_int(days, low=1, high=90, label="Expiry")
    existing = userconfig.load().get("cloud_preapprovals") or []
    existing.append({
        "category": str(category).strip(),
        "path": str(folder.resolve()),
        "expires_at": time.time() + span * 86_400,
    })
    userconfig.update(cloud_preapprovals=existing)
    return f"{category} tasks under {folder} for {span} day(s)"


def add_handle(handle: str) -> list[str]:
    """Add one handle to the allowlist, atomically.

    Takes the *intent*, not a computed list, and that distinction is the whole
    point. The settings window used to send the new list it had worked out
    itself — `existing + [new]` — which silently loses data whenever its copy
    of `existing` is stale or has not loaded yet. Opening the window and typing
    a handle before the first reply arrived wrote a list containing only that
    handle, discarding every other one.

    Reading and writing here means the daemon, which owns the file, is the only
    thing that ever decides what the list becomes.
    """
    current = list(userconfig.load().get("allowed_handles", []))
    cleaned = _validate_handles([*current, handle])
    userconfig.update(allowed_handles=cleaned)
    return cleaned


def remove_handle(handle: str) -> list[str]:
    """Remove one handle from the allowlist, atomically. See `add_handle`."""
    current = list(userconfig.load().get("allowed_handles", []))
    remaining = [h for h in current if h != handle.strip()]
    if remaining == current:
        raise SettingRejected(f"{handle!r} is not on the allowlist.")
    userconfig.update(allowed_handles=remaining)
    return remaining


def revoke_credentials() -> str:
    """Delete every stored credential, leaving the install in place.

    The in-app half of `scripts/uninstall.sh`. It stops short of removing the
    app or its data on purpose: an app deleting itself while running is fragile,
    and quietly erasing someone's audit log — the record of what MACman did —
    is not something a single button should do.

    What it *can* do completely is revoke access, which is the part that
    matters if you have stopped trusting it.
    """
    import keyring

    removed = []
    if auth.is_configured():
        auth.revoke()
        removed.append("login code")
    if keyring.get_password(CLOUD_KEYCHAIN_SERVICE, _CLOUD_ACCOUNT) is not None:
        clear_cloud_key()
        removed.append("Claude key")

    if not removed:
        return "No credentials were stored."

    note = " and ".join(removed)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return (f"Deleted the {note}. ANTHROPIC_API_KEY is still set in the "
                "environment, which no app can unset — remove it from your "
                "shell profile or .env.")
    return f"Deleted the {note}. MACman can no longer authenticate anyone."


def remove_pre_approval(index: int) -> None:
    existing = userconfig.load().get("cloud_preapprovals") or []
    if not 0 <= index < len(existing):
        raise SettingRejected("That pre-approval no longer exists.")
    existing.pop(index)
    userconfig.update(cloud_preapprovals=existing)

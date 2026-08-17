"""Permissions, expressed as capabilities rather than a checklist.

MACman asks for six macOS permissions. Demanding all of them before it will do
anything is the "grant everything just in case" pattern, and for a tool holding
Full Disk Access that is exactly the wrong posture — the trust cost is real and
the technical need is not.

So permissions are modelled the other way round: **each feature declares what it
needs**, and the user is told what works now, what doesn't, and what granting
one more thing would unlock. Nothing is requested until the feature is used.

Two consequences worth stating:

* **Every permission is optional.** MACman with none of them still answers
  questions about files and runs shell-level work. Each grant adds a feature
  rather than unlocking the product.
* **Refusal is a supported state, not an error.** A user who never grants
  Screen Recording should see "screenshots are off", not a warning badge.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import Quartz
from ApplicationServices import AXIsProcessTrusted

_SETTINGS = "x-apple.systempreferences:com.apple.preference.security"


@dataclass(frozen=True)
class Permission:
    key: str
    name: str
    #: System Settings pane anchor.
    pane: str
    #: Plain-language reason, shown at the moment it's requested.
    because: str

    @property
    def url(self) -> str:
        return f"{_SETTINGS}?{self.pane}"

    def granted(self) -> bool:
        return _PROBES[self.key]()


def _probe_accessibility() -> bool:
    return bool(AXIsProcessTrusted())


def _probe_screen_recording() -> bool:
    return bool(Quartz.CGPreflightScreenCaptureAccess())


def _probe_full_disk() -> bool:
    """Attempt a real read of the Messages database.

    There is no API for Full Disk Access, and `os.access` reports readable for
    paths TCC blocks at open time. Only an actual read is conclusive.
    """
    try:
        with (Path.home() / "Library/Messages/chat.db").open("rb") as handle:
            handle.read(1)
    except OSError:
        return False
    return True


def _probe_automation() -> bool:
    """Whether at least one app can be scripted.

    Automation is granted per-app, so there is no single yes/no. Finder is the
    proxy: if it answers, the mechanism works and other apps will prompt
    individually as they're first used.
    """
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e",
             'tell application "Finder" to return name of home'],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _probe_speech() -> bool:
    """Ask the speech helper, which is the only thing that knows.

    Reported by the helper rather than probed here, because the grant belongs
    to whichever binary requested it.
    """
    from macman.voice import speech

    return speech.status().available


_PROBES = {
    "accessibility": _probe_accessibility,
    "screen_recording": _probe_screen_recording,
    "full_disk": _probe_full_disk,
    "automation": _probe_automation,
    "speech": _probe_speech,
}


PERMISSIONS: dict[str, Permission] = {
    p.key: p for p in (
        Permission("full_disk", "Full Disk Access", "Privacy_AllFiles",
                   "so MACman can read incoming iMessages. This is the broadest "
                   "permission macOS has — grant it only if you want to text "
                   "your Mac."),
        Permission("automation", "Automation", "Privacy_Automation",
                   "so MACman can ask apps like Mail, Calendar and Notes for "
                   "information. Approved one app at a time, as each is used."),
        Permission("accessibility", "Accessibility", "Privacy_Accessibility",
                   "for screen brightness and, eventually, driving apps that "
                   "have no other automation."),
        Permission("screen_recording", "Screen Recording", "Privacy_ScreenCapture",
                   "so replies can include a screenshot of what happened."),
        Permission("speech", "Microphone & Speech", "Privacy_Microphone",
                   "for talking to your Mac out loud. Transcription runs "
                   "on-device — nothing is uploaded."),
    )
}


@dataclass(frozen=True)
class Capability:
    name: str
    needs: tuple[str, ...]
    without: str

    def available(self) -> bool:
        return all(PERMISSIONS[key].granted() for key in self.needs)

    def missing(self) -> list[Permission]:
        return [PERMISSIONS[key] for key in self.needs
                if not PERMISSIONS[key].granted()]


#: What MACman can do, and what each thing actually requires. Ordered by how
#: much of the product they represent.
CAPABILITIES: tuple[Capability, ...] = (
    Capability("Files, folders and system control", (),
               "always available — needs no permissions at all"),
    Capability("Developer tools (VS Code, Claude Code)", (),
               "always available"),
    Capability("Mail, Calendar, Notes, Reminders", ("automation",),
               "those apps can't be asked for anything"),
    Capability("Music, browsers, Pages/Numbers/Keynote", ("automation",),
               "those apps can't be controlled"),
    Capability("Text your Mac over iMessage", ("full_disk", "automation"),
               "MACman can't read incoming messages, so the text channel is off"),
    Capability("Talk to your Mac out loud", ("speech",),
               "voice control is off; text and the CLI still work"),
    Capability("Screenshots attached to replies", ("screen_recording",),
               "replies are text only"),
    Capability("Screen brightness", ("accessibility",),
               "brightness can't be changed; everything else in system control works"),
)


def summary() -> tuple[list[Capability], list[Capability]]:
    """Split capabilities into working and not-yet-available."""
    working = [c for c in CAPABILITIES if c.available()]
    blocked = [c for c in CAPABILITIES if not c.available()]
    return working, blocked


def unlocks(key: str) -> list[Capability]:
    """What granting one permission would turn on."""
    return [c for c in CAPABILITIES
            if key in c.needs and not c.available()
            and all(PERMISSIONS[k].granted() for k in c.needs if k != key)]


def open_settings(key: str) -> None:
    subprocess.run(["open", PERMISSIONS[key].url], check=False)


def require(key: str) -> str | None:
    """Check a permission at the moment a feature needs it.

    Returns None when granted, or a sentence explaining what to do. Called from
    features rather than at startup, so a user is only ever asked for something
    at the moment it would actually be used.
    """
    permission = PERMISSIONS[key]
    if permission.granted():
        return None
    return (f"This needs {permission.name} — {permission.because} "
            f"Grant it under System Settings → Privacy & Security → "
            f"{permission.name}, then try again.")

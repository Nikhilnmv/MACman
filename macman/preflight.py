"""Permission preflight.

MACman needs five separate TCC grants, each requiring a manual approval in
System Settings. This module reports which are missing and deep-links to the
exact pane, so setup is a checklist rather than a scavenger hunt.

    python -m macman.preflight

One thing worth knowing up front: **TCC grants attach to the calling binary's
code signature.** During development that is your terminal, not MACman. An
unsigned binary can re-trigger every prompt on each rebuild, which is why
DESIGN.md §8 calls for a self-signed identity early.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import Quartz
from ApplicationServices import AXIsProcessTrusted

from macman.security import lockstate

_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security"


@dataclass(frozen=True)
class Check:
    name: str
    granted: bool
    needed_for: str
    #: System Settings pane anchor, or None where no pane applies.
    pane: str | None

    @property
    def url(self) -> str | None:
        return f"{_SETTINGS_URL}?{self.pane}" if self.pane else None


def _probe_full_disk_access() -> bool:
    """Attempt an actual read of the Messages database.

    There is no API for Full Disk Access, and `os.access` lies here — it reports
    readable for paths TCC blocks at open time. Only a real read is conclusive.
    """
    chat_db = Path.home() / "Library/Messages/chat.db"
    try:
        with chat_db.open("rb") as handle:
            handle.read(1)
    except OSError:
        return False
    return True


def run_checks() -> list[Check]:
    return [
        Check("Accessibility", AXIsProcessTrusted(),
              "UI automation (tier 3), FaceTime driver", "Privacy_Accessibility"),
        Check("Screen Recording", Quartz.CGPreflightScreenCaptureAccess(),
              "screenshots, virtual camera", "Privacy_ScreenCapture"),
        Check("Full Disk Access", _probe_full_disk_access(),
              "reading chat.db for the iMessage channel", "Privacy_AllFiles"),
        # Microphone and Speech Recognition are requested at first use by the
        # Swift helpers; there is no read-only probe that doesn't also prompt.
        Check("Microphone", False, "audio capture (v2)", "Privacy_Microphone"),
        Check("Speech Recognition", False, "on-device STT (v2)",
              "Privacy_SpeechRecognition"),
    ]


def _report_engine() -> None:
    """Report whether the on-device engine can actually use tools.

    Worth its own check because the failure is silent and total. The helper
    only gets tool support when built with `-DMACMAN_TOOLS`, which needs full
    Xcode; a plain `swift build` produces a binary that compiles, runs, reports
    itself available, and then answers every question from the model's own head
    instead of looking anything up.

    Nothing warns you. A rebuild without the flag took tool selection from 99%
    to 0% here, and the only visible symptom was MACman becoming vague and
    unhelpful. That is worth one line at startup.
    """
    from macman.engines import local as local_engine

    try:
        backend = local_engine.apple_backend()
    except Exception:
        return  # never let a status line break preflight

    if not backend.available:
        print(f"  Engine    on-device model unavailable — {backend.detail}\n")
        return
    if not backend.tools:
        print("  Engine    ⚠ on-device model READY BUT WITHOUT TOOLS\n")
        print("            It cannot count files, read folders or open apps;")
        print("            it will answer from memory instead of looking.")
        print("            Rebuild with the flag that enables tool support:")
        print("              cd helpers && swift build -c release "
              "-Xswiftc -DMACMAN_TOOLS\n")
        return
    print("  Engine    on-device model ready, tools enabled\n")


def main() -> int:
    """Report what MACman can do right now, and what one more grant unlocks.

    Deliberately framed as capabilities rather than a checklist of missing
    permissions. Every permission is optional; each adds a feature rather than
    unlocking the product, and a user who declines one should see a fact, not
    a warning.
    """
    from macman.security import permissions

    state = lockstate.read()
    working, blocked = permissions.summary()

    print("MACman\n")
    print(f"  Session   {state.user_name or '(none)'}")
    print(f"  Tier      {state.tier.value} — {state.explain()}\n")

    _report_engine()

    print(f"  Working now ({len(working)})")
    for capability in working:
        print(f"    ✓ {capability.name}")

    if blocked:
        print(f"\n  Not available ({len(blocked)})")
        for capability in blocked:
            needed = ", ".join(p.name for p in capability.missing())
            print(f"    · {capability.name}")
            print(f"        {capability.without}")
            print(f"        needs: {needed}")

        print("\n  To turn something on:")
        seen: set[str] = set()
        for capability in blocked:
            for permission in capability.missing():
                if permission.key in seen:
                    continue
                seen.add(permission.key)
                unlocked = permissions.unlocks(permission.key)
                if unlocked:
                    print(f"\n    {permission.name} — {permission.because}")
                    print(f"      unlocks: {', '.join(c.name for c in unlocked)}")
                    print(f"      open '{permission.url}'")

    print("\n  Nothing here is required. MACman works with none of these "
          "granted;\n  each one adds a feature.")
    return 0


def open_pane(pane: str) -> None:
    """Open a System Settings privacy pane directly."""
    subprocess.run(["open", f"{_SETTINGS_URL}?{pane}"], check=False)


if __name__ == "__main__":
    sys.exit(main())

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


def main() -> int:
    state = lockstate.read()
    checks = run_checks()
    missing = [c for c in checks if not c.granted]

    print("MACman preflight\n")
    print(f"  Session   {state.user_name or '(none)'}")
    print(f"  Tier      {state.tier.value} — {state.explain()}\n")

    print("  Permissions")
    for check in checks:
        mark = "OK  " if check.granted else "    "
        print(f"    [{mark}] {check.name:<20} {check.needed_for}")

    if not missing:
        print("\nAll permissions granted.")
        return 0

    print(f"\n{len(missing)} permission(s) missing. Open each pane and add your terminal:\n")
    for check in missing:
        print(f"  {check.name}")
        if check.url:
            print(f"    open '{check.url}'")

    print(
        "\nNote: grants attach to the *calling* binary. Granting these to your terminal "
        "covers development; MACman.app will need its own grants at v4."
    )
    return 1


def open_pane(pane: str) -> None:
    """Open a System Settings privacy pane directly."""
    subprocess.run(["open", f"{_SETTINGS_URL}?{pane}"], check=False)


if __name__ == "__main__":
    sys.exit(main())

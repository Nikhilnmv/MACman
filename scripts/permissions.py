#!/usr/bin/env python3
"""What macOS permissions are actually in effect, and who holds them.

    .venv/bin/python scripts/permissions.py

Run it from **different places** to learn different things, because that is how
macOS works:

    from Terminal          → what Terminal can do
    from MACman.app        → what MACman can do
    from an editor's shell → what that editor can do

## Why "who holds them" is the whole question

macOS attributes a permission to the **responsible process** — the app that
launched the one asking, not the program doing the asking. A Python script run
from Terminal has Terminal's permissions. Granting Full Disk Access "to
MACman" from a terminal does not give it to MACman; it gives it to Terminal,
and therefore to **every script you will ever run in a shell**.

That is why MACman.app exists, and why this script names the responsible app
rather than reporting a bare yes/no.

## What this cannot do

**It cannot read the permission database.** `TCC.db` is protected by macOS —
deliberately, since a program able to enumerate its own oversight is halfway to
disabling it. So every result here is a **behavioural probe**: it tries the
thing and reports what happened. That is more honest than reading a database
anyway, because it reflects what is truly possible right now.

**It cannot revoke anything.** It prints the commands, and you run them. A
program that could switch off its own supervision would be exactly the wrong
design.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macman.security import permissions  # noqa: E402

#: Bundle identifiers worth checking by name, with why each might hold grants.
KNOWN_HOLDERS = {
    "com.nikhilnmv.macman": (
        "MACman.app",
        "Should hold them. This is the point of the app."),
    "com.apple.Terminal": (
        "Terminal",
        "Covers EVERY script you run in a shell, not just MACman."),
    "com.googlecode.iterm2": (
        "iTerm2",
        "Same breadth as Terminal."),
    "com.anthropic.claudefordesktop": (
        "Claude",
        "Anything Claude Code runs inherits these."),
    "com.microsoft.VSCode": (
        "VS Code",
        "Covers its integrated terminal and every extension."),
}


def responsible_app() -> tuple[str, str]:
    """Walk up the process tree to the app that owns this process's grants.

    Returns (display name, bundle id or ""). The first `.app` ancestor is the
    responsible process in practice — that is what TCC attributes to.
    """
    pid = os.getpid()
    for _ in range(10):
        result = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                capture_output=True, text=True)
        if not result.stdout.strip():
            break
        parent, command = result.stdout.strip().split(None, 1)

        if ".app/Contents/MacOS/" in command:
            bundle_path = command.split("/Contents/MacOS/")[0]
            name = Path(bundle_path).name
            # Python.framework ships its own stub .app; it is not a host.
            if name != "Python.app":
                identifier = subprocess.run(
                    ["defaults", "read", f"{bundle_path}/Contents/Info",
                     "CFBundleIdentifier"],
                    capture_output=True, text=True).stdout.strip()
                return name, identifier

        pid = int(parent)
        if pid <= 1:
            break
    return "a terminal or launchd (no .app ancestor found)", ""


#: What MACman genuinely needs, versus what is optional. Stated so a reader can
#: revoke the rest without guessing which one breaks something.
NEEDED = {
    "full_disk": "Required for the text channel — chat.db is protected.",
    "automation": "Required for Mail, Calendar, Notes, Reminders, browsers.",
    "accessibility": "Optional. Only screen brightness uses it today.",
    "screen_recording": "Optional. Only for screenshots attached to replies.",
    "speech": "Optional. Only for voice mode.",
}


def _speech_detail() -> list[tuple[str, str]]:
    """The microphone and speech-recognition grants, separately."""
    import json

    from macman.voice import speech

    helper = speech.helper_path()
    if helper is None:
        return [("detail", "speech helper not built")]
    try:
        result = subprocess.run([str(helper), "check"], capture_output=True,
                                text=True, timeout=30)
        report = json.loads(result.stdout.strip().splitlines()[-1])
    except Exception:                            # noqa: BLE001
        return [("detail", "helper did not respond")]
    return [("Microphone", report.get("microphone", "?")),
            ("Speech Recognition", report.get("speechRecognition", "?"))]


def main() -> int:
    name, identifier = responsible_app()

    print("\nmacOS permissions — what is in effect right now\n")
    print(f"  Running under: {name}" + (f"  ({identifier})" if identifier else ""))
    print("  Everything below describes what THAT app can do, not what MACman")
    print("  can do — unless they are the same app.\n")

    granted, denied = [], []
    for key, permission in permissions.PERMISSIONS.items():
        ok = permission.granted()
        (granted if ok else denied).append((key, permission))
        mark = "GRANTED" if ok else "   —   "
        print(f"  [{mark}] {permission.name}")
        print(f"            {NEEDED.get(key, '')}")

        # "Microphone & Speech" is two separate grants that macOS tracks
        # independently, and they genuinely diverge: a host can hold the
        # microphone while speech recognition is still undetermined. Collapsing
        # them into one line would report access that does not exist — not a
        # rounding error in a document whose only job is being exact.
        if key == "speech":
            for label, value in _speech_detail():
                print(f"              · {label}: {value}")

    print(f"\n  {len(granted)} granted, {len(denied)} not granted.")

    if identifier == "com.nikhilnmv.macman":
        print("\n  These belong to MACman.app specifically, which is the design:")
        print("  revoking them affects nothing else on your Mac.")
    elif granted:
        print(f"\n  ⚠ These belong to {name}, not to MACman.")
        print(f"    Anything {name} launches inherits them — every script, every")
        print("    extension, every tool. That is much broader than MACman needs.")

    print("\n" + "─" * 70)
    print("  Revoking — commands for you to run, not for this script\n")
    print("  Point-and-click, and the only way to see every app at once:")
    print("    open 'x-apple.systempreferences:com.apple.preference.security"
          "?Privacy_AllFiles'\n")
    print("  Or reset one app's permissions from a terminal:\n")
    holders = dict(KNOWN_HOLDERS)
    if identifier and identifier not in holders:
        # The host that actually launched us matters more than any hard-coded
        # list. Claude Code, for instance, reports com.anthropic.claude-code
        # rather than the desktop app's identifier, so a fixed list would send
        # someone to reset the wrong bundle and wonder why nothing changed.
        holders[identifier] = (name, "The app that launched this check.")

    for bundle, (label, why) in holders.items():
        installed = subprocess.run(
            ["mdfind", f"kMDItemCFBundleIdentifier == '{bundle}'"],
            capture_output=True, text=True).stdout.strip()
        if not installed and bundle != "com.nikhilnmv.macman":
            continue
        print(f"    # {label} — {why}")
        print(f"    tccutil reset All {bundle}\n")

    print("  `tccutil reset All` clears every permission for that app; it will")
    print("  ask again next time it needs one. Nothing is destroyed, and it is")
    print("  the cleanest way to undo a grant you no longer want.\n")
    print("  Read the full guide: docs/PERMISSIONS.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Experiment: is FaceTime drivable without a second Apple device present?

    .venv/bin/python tests/tasks/facetime_probe.py

FaceTime ships no `.sdef`, so AppleScript is out and the only candidates are
URL schemes and the Accessibility tree. Before writing a call driver against
either, this measures what actually exists — the alternative is building on an
assumption and discovering it during a live call.

**This probe is read-only.** It opens FaceTime and reads its Accessibility
tree. It places no call, presses no button, and changes no preference.

Three questions, only the first two answerable without a second device:

1. Can we read FaceTime's window structure at all? (Accessibility permission,
   and whether the tree is populated rather than an opaque single node.)
2. Is there a control we could press to answer a call, addressable by *path*
   rather than by pixel coordinate?
3. Does an incoming call actually surface those controls? — needs a real call.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from AppKit import NSWorkspace  # noqa: E402
from ApplicationServices import (  # noqa: E402
    AXIsProcessTrusted,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
)

BUNDLE = "com.apple.FaceTime"
_LABEL_ATTRS = ("AXTitle", "AXDescription", "AXValue", "AXHelp")

#: Words that would appear on a control worth pressing during a call.
INTERESTING = ("accept", "answer", "decline", "end", "join", "mute",
               "video", "audio", "call", "leave")


def _attr(element, name):
    err, value = AXUIElementCopyAttributeValue(element, name, None)
    return None if err else value


def _label(element) -> str:
    for attr in _LABEL_ATTRS:
        value = _attr(element, attr)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def walk(element, depth: int = 0, max_depth: int = 8):
    """Yield (depth, role, label) for every node, depth-first."""
    role = _attr(element, "AXRole") or "?"
    yield depth, str(role), _label(element)
    if depth >= max_depth:
        return
    for child in (_attr(element, "AXChildren") or []):
        yield from walk(child, depth + 1, max_depth)


def pid_for_bundle(bundle: str) -> int | None:
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.bundleIdentifier() == bundle:
            return app.processIdentifier()
    return None


def main() -> int:
    print("FaceTime drivability probe — read-only\n")

    if not AXIsProcessTrusted():
        print("  BLOCKED: Accessibility permission not granted to this process.")
        print("  System Settings → Privacy & Security → Accessibility.")
        print("  Grant it to the app running Python (Terminal), not Python itself.")
        return 2

    print("── Q1. Can we see FaceTime's UI at all?")

    # Three separate waits, because the process existing, the window existing,
    # and the window being *populated* happen at different times. An earlier
    # version waited only for the process and reported "tree is opaque" on a
    # window that simply had not been built yet — three consecutive runs gave
    # "did not start", "0 windows", then 19 nodes. That is a race in the probe,
    # not a finding about FaceTime, and it would have been recorded as one.
    pid = pid_for_bundle(BUNDLE)
    if pid is None:
        print("   FaceTime not running; opening it.")
        subprocess.run(["open", "-a", "FaceTime"], check=False)
        for _ in range(40):
            time.sleep(0.5)
            if (pid := pid_for_bundle(BUNDLE)) is not None:
                break
    if pid is None:
        print("   FAIL — FaceTime did not start.")
        return 1
    print(f"   FaceTime running, pid {pid}")

    app = AXUIElementCreateApplication(pid)

    windows = []
    for _ in range(40):
        windows = _attr(app, "AXWindows") or []
        if windows:
            break
        time.sleep(0.5)
    print(f"   windows visible to Accessibility: {len(windows)}")
    if not windows:
        print("   FAIL — no window after 20s. FaceTime may be running with no")
        print("   UI shown; bring it to the front and re-run.")
        return 1

    nodes = []
    for _ in range(40):
        nodes = [node for window in windows for node in walk(window)]
        if len(nodes) > 1:
            break
        time.sleep(0.5)

    print(f"   nodes in the tree: {len(nodes)}")
    if len(nodes) <= 1:
        print("   FAIL — tree is opaque. Accessibility cannot drive this.")
        return 1
    print("   PASS — the tree is populated and readable.\n")

    print("── Tree (roles with labels only)")
    shown = 0
    for depth, role, label in nodes:
        if label and shown < 60:
            print(f"   {'  ' * depth}{role}: {label[:58]}")
            shown += 1
    if shown == 0:
        print("   (no labelled nodes — every element is unlabelled)")
    print()

    print("── Q2. Any control we could press to answer a call?")
    buttons = [(d, r, l) for d, r, l in nodes if "Button" in r]
    print(f"   buttons found: {len(buttons)}")
    for _, role, label in buttons[:25]:
        print(f"     {role}: {label or '(unlabelled)'}")

    hits = [l for _, _, l in nodes
            if l and any(word in l.lower() for word in INTERESTING)]
    if hits:
        print(f"\n   call-related labels: {', '.join(sorted(set(hits))[:10])}")
    else:
        print("\n   No call-related labels in the idle window — expected. "
              "Answer controls appear only during an incoming call, which is "
              "question 3 and needs a second device.")

    unlabelled = sum(1 for _, r, l in nodes if "Button" in r and not l)
    labelled = len(buttons) - unlabelled

    print(f"\n   labelled: {labelled}   unlabelled: {unlabelled}")
    if unlabelled:
        print("\n   RISK: an unlabelled button can only be addressed by its")
        print("   position in the tree. Positions shift between macOS releases,")
        print("   and there is no label to confirm we pressed the right thing —")
        print("   which is the same weakness that measured 50% in")
        print("   RELIABILITY.md and got Accessibility clicking dropped.")
        print("   Answering a call this way is only acceptable if Accept turns")
        print("   out to be *labelled*. That is question 3.")

    print("\n" + "─" * 68)
    print("  Q1 readable tree      : PASS")
    # Deliberately not a flat PASS: buttons existing is not the same as being
    # safely pressable, and the honest verdict depends on whether the control
    # that matters carries a label.
    if not buttons:
        verdict = "FAIL — nothing pressable"
    elif unlabelled:
        verdict = (f"PARTIAL — {len(buttons)} buttons, {unlabelled} unlabelled")
    else:
        verdict = f"PASS — {len(buttons)} buttons, all labelled"
    print(f"  Q2 pressable controls : {verdict}")
    print("  Q3 incoming-call UI   : BLOCKED — needs a second Apple device")
    print("\n  Q3 is the one that decides this. If Accept is labelled, calls can")
    print("  be answered under allowlist control. If not, the fallback is")
    print("  AutoAcceptInvites, which answers *everyone* and is a worse trade.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

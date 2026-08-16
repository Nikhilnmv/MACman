#!/usr/bin/env python3
"""Measure which operations survive a locked screen.

This is the v0 experiment flagged in DESIGN.md §6.3. It decides how useful
headless mode actually is — and therefore how hard MACman should lean on
scripting over UI automation for the private task set.

Run it twice:

    # 1. Baseline, screen unlocked. Also grants any Automation prompts.
    .venv/bin/python tests/tasks/locked_boundary.py --out baseline.json

    # 2. Locked. Locks the screen itself, waits for it to register, probes,
    #    and writes results. Unlock afterwards to read them.
    .venv/bin/python tests/tasks/locked_boundary.py --self-lock --out locked.json

    # 3. Compare.
    .venv/bin/python tests/tasks/locked_boundary.py --compare baseline.json locked.json

**Run the baseline first.** Automation permission prompts cannot be answered
behind a lock screen, so an ungranted app would look like a locked-screen
failure when it is really a permission failure.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from macman.agent.tools import applescript, shell, ui  # noqa: E402
from macman.security import lockstate  # noqa: E402


@dataclass
class Probe:
    name: str
    #: Which tool tier this exercises, so results map onto the capability table.
    tier: str
    run: object = None


@dataclass
class Result:
    name: str
    tier: str
    ok: bool
    detail: str


# --------------------------------------------------------------------------- #
# Probes
# --------------------------------------------------------------------------- #


def _script(source: str):
    def run() -> tuple[bool, str]:
        result = applescript.run(source)
        return result.ok, (result.output or "")[:90].replace("\n", " ")
    return run


def _shell(command: str):
    def run() -> tuple[bool, str]:
        result = shell.run(command, timeout=15)
        return result.ok, (result.output or "")[:90].replace("\n", " ")
    return run


def _ax(app: str):
    def run() -> tuple[bool, str]:
        try:
            tree = ui.query(app, max_depth=3)
        except ui.AccessibilityError as exc:
            return False, str(exc)[:90]
        count = len(tree.get("children", ()))
        return count > 0, f"{count} top-level children"
    return run


PROBES: list[Probe] = [
    # Tier 1 — expected to survive a lock entirely.
    Probe("shell: read a file", "1", _shell("head -c 40 /etc/hosts")),
    Probe("shell: write a file", "1", _shell("echo probe > /tmp/macman_probe && cat /tmp/macman_probe")),
    Probe("shell: git", "1", _shell("git --version")),
    Probe("shell: launch an app", "1", _shell("open -a TextEdit && echo launched")),
    Probe("shell: screencapture", "1", _shell("screencapture -x /tmp/macman_probe.png && echo captured")),

    # Tier 2 — the interesting boundary. App scripting should mostly survive;
    # System Events UI scripting should not.
    Probe("as: pure AppleScript", "2", _script('return (system attribute "sysv") as text')),
    Probe("as: Finder home path", "2", _script('tell application "Finder" to return POSIX path of (home as alias)')),
    Probe("as: Finder desktop count", "2", _script('tell application "Finder" to return count of items of desktop')),
    Probe("as: Mail unread count", "2", _script('tell application "Mail" to return unread count of inbox')),
    Probe("as: Calendar names", "2", _script('tell application "Calendar" to return name of every calendar')),
    Probe("as: Notes count", "2", _script('tell application "Notes" to return count of notes')),
    Probe("as: Reminders lists", "2", _script('tell application "Reminders" to return name of every list')),
    Probe("as: Pages doc count", "2", _script('tell application "Pages" to return count of documents')),
    Probe("as: TextEdit doc count", "2", _script('tell application "TextEdit" to return count of documents')),
    Probe("as: System Events (UI)", "2", _script('tell application "System Events" to return name of first process whose frontmost is true')),

    # Tier 3 — expected to fail when locked.
    Probe("ax: Finder tree", "3", _ax("Finder")),
]


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #


def run_probes() -> dict:
    state = lockstate.read()
    print(f"  tier: {state.tier.value} (screen_locked={state.screen_locked})\n")

    results: list[Result] = []
    for probe in PROBES:
        try:
            ok, detail = probe.run()
        except Exception as exc:  # a probe must never abort the run
            ok, detail = False, f"{type(exc).__name__}: {exc}"[:90]
        results.append(Result(probe.name, probe.tier, ok, detail))
        print(f"  [{'ok  ' if ok else 'FAIL'}] t{probe.tier}  {probe.name:<30} {detail[:52]}")

    return {
        "tier": state.tier.value,
        "screen_locked": state.screen_locked,
        "results": [asdict(r) for r in results],
    }


def lock_screen() -> None:
    """Lock via the Keychain menu's underlying command.

    `pmset displaysleepnow` only sleeps the display; whether that locks depends
    on the 'require password after sleep' delay. This locks outright.
    """
    subprocess.run(
        ["/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession",
         "-suspend"],
        check=False,
    )


def wait_for_lock(timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if lockstate.read().tier is lockstate.Tier.HEADLESS:
            return True
        time.sleep(0.5)
    return False


def compare(baseline_path: Path, locked_path: Path) -> int:
    baseline = json.loads(baseline_path.read_text())
    locked = json.loads(locked_path.read_text())

    by_name = {r["name"]: r for r in locked["results"]}
    survived, lost, already_failing = [], [], []

    for entry in baseline["results"]:
        other = by_name.get(entry["name"])
        if other is None:
            continue
        if not entry["ok"]:
            already_failing.append(entry)
        elif other["ok"]:
            survived.append(entry)
        else:
            lost.append((entry, other))

    print(f"\n{'─' * 66}\nLocked-screen capability boundary\n{'─' * 66}")
    print(f"\n  SURVIVES A LOCK ({len(survived)})")
    for entry in survived:
        print(f"    t{entry['tier']}  {entry['name']}")

    print(f"\n  LOST WHEN LOCKED ({len(lost)})")
    for entry, other in lost:
        print(f"    t{entry['tier']}  {entry['name']:<30} {other['detail'][:40]}")

    if already_failing:
        print(f"\n  INCONCLUSIVE — failed in the baseline too ({len(already_failing)})")
        for entry in already_failing:
            print(f"    t{entry['tier']}  {entry['name']:<30} {entry['detail'][:40]}")

    tier2 = [e for e in survived if e["tier"] == "2"]
    baseline_tier2 = [e for e in baseline["results"] if e["tier"] == "2" and e["ok"]]
    if baseline_tier2:
        pct = 100 * len(tier2) / len(baseline_tier2)
        print(f"\n  Tier 2 survival: {len(tier2)}/{len(baseline_tier2)} ({pct:.0f}%)")
        print("  This is the number that decides how useful headless mode is.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write results as JSON")
    parser.add_argument("--self-lock", action="store_true",
                        help="lock the screen, wait, then probe")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BASELINE", "LOCKED"))
    args = parser.parse_args()

    if args.compare:
        return compare(*args.compare)

    if args.self_lock:
        print("  Locking the screen in 3s. Do not touch the Mac until it finishes;")
        print("  results are written to disk, so unlock afterwards to read them.\n")
        time.sleep(3)
        lock_screen()
        if not wait_for_lock():
            print("  Screen did not report as locked — aborting.")
            return 1
        time.sleep(2)  # let the window server settle

    data = run_probes()

    if args.out:
        args.out.write_text(json.dumps(data, indent=2))
        print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

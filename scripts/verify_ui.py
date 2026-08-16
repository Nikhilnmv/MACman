#!/usr/bin/env python3
"""Verify tier 3 (Accessibility) against live apps.

Run this from a terminal that has been granted Accessibility permission —
the grant attaches to the terminal app, not to Python.

    .venv/bin/python scripts/verify_ui.py
    .venv/bin/python scripts/verify_ui.py --press   # also exercise press/set_value

The `--press` pass opens a scratch TextEdit document, writes to it through the
Accessibility API, reads it back, and closes it discarding changes. It is
reversible, but it does open and close a window, so it is opt-in.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macman.agent.tools import applescript, ui  # noqa: E402


def _rule(title: str) -> None:
    print(f"\n{'─' * 62}\n{title}\n{'─' * 62}")


def check_trust() -> bool:
    _rule("1. Accessibility trust")
    try:
        ui.ensure_trusted()
    except ui.AccessibilityError as exc:
        print(f"  FAIL  {exc}")
        print("\n  Grant Accessibility to THIS terminal app, then re-run.")
        return False
    print("  PASS  process is trusted")
    return True


def check_query() -> bool:
    """Walk a real tree. Finder is always running, so it is the safe default."""
    _rule("2. Tree query (Finder)")
    try:
        tree = ui.query("Finder", max_depth=4)
    except ui.AccessibilityError as exc:
        print(f"  FAIL  {exc}")
        return False

    def count(node: dict) -> int:
        return 1 + sum(count(child) for child in node.get("children", ()))

    total = count(tree)
    print(f"  PASS  {total} nodes")
    print("\n  Sample (first 900 chars):")
    for line in json.dumps(tree, indent=2)[:900].splitlines():
        print(f"    {line}")
    return total > 1


def check_find() -> bool:
    """`find` is what the model actually calls; it must return usable paths."""
    _rule("3. Find by role (menu bar items)")
    try:
        items = ui.find("Finder", role="AXMenuBarItem")
    except ui.AccessibilityError as exc:
        print(f"  FAIL  {exc}")
        return False

    if not items:
        print("  FAIL  no menu bar items found — tree walk is not reaching them")
        return False

    print(f"  PASS  {len(items)} menu bar items")
    for element in items[:8]:
        print(f"    path={element.path:<10} {element.role:<16} {element.label!r}")
    return True


def check_resolve() -> bool:
    """A path from `find` must resolve back to a live element."""
    _rule("4. Path round-trip")
    try:
        items = ui.find("Finder", role="AXMenuBarItem")
        if not items:
            print("  SKIP  nothing to resolve")
            return True
        target = items[0]
        element = ui.resolve("Finder", target.path)
        role = ui._attr(element, "AXRole")
        label = ui._label_of(element)
    except ui.AccessibilityError as exc:
        print(f"  FAIL  {exc}")
        return False

    ok = role == target.role
    print(f"  {'PASS' if ok else 'FAIL'}  {target.path!r} -> role={role} label={label!r}")
    return ok


def check_press() -> bool:
    """Round-trip press and set_value through a scratch TextEdit document."""
    _rule("5. Press / set_value (TextEdit scratch document)")
    marker = "macman-ax-verification"

    result = applescript.run(
        'tell application "TextEdit"\n'
        '  activate\n'
        '  make new document\n'
        'end tell'
    )
    if not result.ok:
        print(f"  FAIL  could not open TextEdit: {result.output[:140]}")
        return False

    try:
        areas = ui.find("TextEdit", role="AXTextArea")
        if not areas:
            print("  FAIL  no AXTextArea in the new document")
            return False

        path = areas[0].path
        ui.set_value("TextEdit", path, marker)

        element = ui.resolve("TextEdit", path)
        read_back = ui._attr(element, "AXValue")
        ok = read_back == marker
        print(f"  {'PASS' if ok else 'FAIL'}  wrote and read back {read_back!r} at {path!r}")
        return ok
    except ui.AccessibilityError as exc:
        print(f"  FAIL  {exc}")
        return False
    finally:
        # Discard rather than prompt to save.
        applescript.run(
            'tell application "TextEdit"\n'
            '  if (count of documents) > 0 then close document 1 saving no\n'
            '  quit\n'
            'end tell'
        )
        print("  (scratch document closed without saving)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--press", action="store_true",
                        help="also exercise press/set_value via a scratch TextEdit document")
    args = parser.parse_args()

    if not check_trust():
        return 1

    results = [check_query(), check_find(), check_resolve()]
    if args.press:
        results.append(check_press())

    _rule("Summary")
    passed = sum(results)
    print(f"  {passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

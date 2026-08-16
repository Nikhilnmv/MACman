#!/usr/bin/env python3
"""Can Apple's on-device model navigate an Accessibility tree?

This is the experiment that decides whether the "operate any application" goal
(VISION_FEASIBILITY.md §5) is achievable on the free tier or needs Claude.

The reasoning is different from the typed-tool case already measured. There,
the model picks among 7 named tools and fills 1–2 fields. Here it must read a
nested structure it has never seen, find the one element matching an intent
described in different words than the label, and return its exact path. Tree
size is the variable that matters.

    .venv/bin/python tests/tasks/ax_navigation.py            # synthetic trees
    .venv/bin/python tests/tasks/ax_navigation.py --live     # real apps too

`--live` needs Accessibility permission granted to the terminal running it.
Synthetic trees are modelled on real macOS app structures; they exercise the
same reasoning, and using them keeps the test reproducible.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from macman.engines import local as local_engine  # noqa: E402


@dataclass
class Scenario:
    name: str
    #: Nested AX tree, same shape `ui.query` produces.
    tree: dict
    task: str
    #: The path a correct answer must contain.
    expected: str


def _count(node: dict) -> int:
    return 1 + sum(_count(child) for child in node.get("children", ()))


# --------------------------------------------------------------------------- #
# Trees, modelled on real macOS structures
# --------------------------------------------------------------------------- #

SAVE_DIALOG = {
    "path": "", "role": "AXWindow", "label": "Save changes?",
    "children": [
        {"path": "0", "role": "AXStaticText",
         "label": "Do you want to save the changes made to this document?"},
        {"path": "1", "role": "AXButton", "label": "Save", "enabled": True},
        {"path": "2", "role": "AXButton", "label": "Don't Save", "enabled": True},
        {"path": "3", "role": "AXButton", "label": "Cancel", "enabled": True},
    ],
}

FINDER_WINDOW = {
    "path": "", "role": "AXWindow", "label": "Downloads",
    "children": [
        {"path": "0", "role": "AXToolbar", "children": [
            {"path": "0/0", "role": "AXButton", "label": "Back", "enabled": True},
            {"path": "0/1", "role": "AXButton", "label": "Forward", "enabled": False},
            {"path": "0/2", "role": "AXPopUpButton", "label": "View options"},
            {"path": "0/3", "role": "AXButton", "label": "Group items"},
            {"path": "0/4", "role": "AXButton", "label": "Share", "enabled": True},
            {"path": "0/5", "role": "AXButton", "label": "Add Tags", "enabled": True},
            {"path": "0/6", "role": "AXButton", "label": "New Folder", "enabled": True},
            {"path": "0/7", "role": "AXSearchField", "label": "Search"},
        ]},
        {"path": "1", "role": "AXSplitGroup", "children": [
            {"path": "1/0", "role": "AXOutline", "label": "Sidebar", "children": [
                {"path": "1/0/0", "role": "AXRow", "label": "AirDrop"},
                {"path": "1/0/1", "role": "AXRow", "label": "Recents"},
                {"path": "1/0/2", "role": "AXRow", "label": "Documents"},
                {"path": "1/0/3", "role": "AXRow", "label": "Downloads"},
            ]},
            {"path": "1/1", "role": "AXScrollArea", "label": "File list"},
        ]},
    ],
}

SETTINGS_BLUETOOTH = {
    "path": "", "role": "AXWindow", "label": "System Settings",
    "children": [
        {"path": "0", "role": "AXToolbar", "children": [
            {"path": "0/0", "role": "AXButton", "label": "Back", "enabled": False},
            {"path": "0/1", "role": "AXSearchField", "label": "Search"},
        ]},
        {"path": "1", "role": "AXOutline", "label": "Sidebar", "children": [
            {"path": "1/0", "role": "AXRow", "label": "Wi-Fi"},
            {"path": "1/1", "role": "AXRow", "label": "Bluetooth"},
            {"path": "1/2", "role": "AXRow", "label": "Network"},
            {"path": "1/3", "role": "AXRow", "label": "Notifications"},
            {"path": "1/4", "role": "AXRow", "label": "Sound"},
            {"path": "1/5", "role": "AXRow", "label": "Focus"},
        ]},
        {"path": "2", "role": "AXGroup", "label": "Bluetooth", "children": [
            {"path": "2/0", "role": "AXCheckBox", "label": "Bluetooth", "enabled": True},
            {"path": "2/1", "role": "AXStaticText", "label": "My Devices"},
            {"path": "2/2", "role": "AXGroup", "children": [
                {"path": "2/2/0", "role": "AXStaticText", "label": "Nikhil's AirPods Pro"},
                {"path": "2/2/1", "role": "AXButton", "label": "Connect", "enabled": True},
                {"path": "2/2/2", "role": "AXButton", "label": "Disconnect", "enabled": False},
            ]},
            {"path": "2/3", "role": "AXGroup", "children": [
                {"path": "2/3/0", "role": "AXStaticText", "label": "Magic Keyboard"},
                {"path": "2/3/1", "role": "AXButton", "label": "Connect", "enabled": True},
            ]},
        ]},
    ],
}

PAGES_WINDOW = {
    "path": "", "role": "AXWindow", "label": "Untitled — Pages",
    "children": [
        {"path": "0", "role": "AXToolbar", "children": [
            {"path": "0/0", "role": "AXButton", "label": "Sidebar"},
            {"path": "0/1", "role": "AXButton", "label": "Zoom"},
            {"path": "0/2", "role": "AXButton", "label": "Insert"},
            {"path": "0/3", "role": "AXButton", "label": "Table"},
            {"path": "0/4", "role": "AXButton", "label": "Chart"},
            {"path": "0/5", "role": "AXButton", "label": "Text"},
            {"path": "0/6", "role": "AXButton", "label": "Shape"},
            {"path": "0/7", "role": "AXButton", "label": "Media"},
            {"path": "0/8", "role": "AXButton", "label": "Comment"},
            {"path": "0/9", "role": "AXButton", "label": "Collaborate"},
            {"path": "0/10", "role": "AXButton", "label": "Format"},
            {"path": "0/11", "role": "AXButton", "label": "Document"},
        ]},
        {"path": "1", "role": "AXGroup", "label": "Format panel", "children": [
            {"path": "1/0", "role": "AXRadioButton", "label": "Style"},
            {"path": "1/1", "role": "AXRadioButton", "label": "Layout"},
            {"path": "1/2", "role": "AXRadioButton", "label": "More"},
            {"path": "1/3", "role": "AXGroup", "label": "Font", "children": [
                {"path": "1/3/0", "role": "AXPopUpButton", "label": "Helvetica"},
                {"path": "1/3/1", "role": "AXTextField", "label": "Size"},
                {"path": "1/3/2", "role": "AXCheckBox", "label": "Bold"},
                {"path": "1/3/3", "role": "AXCheckBox", "label": "Italic"},
                {"path": "1/3/4", "role": "AXCheckBox", "label": "Underline"},
            ]},
            {"path": "1/4", "role": "AXGroup", "label": "Alignment", "children": [
                {"path": "1/4/0", "role": "AXButton", "label": "Align Left"},
                {"path": "1/4/1", "role": "AXButton", "label": "Center"},
                {"path": "1/4/2", "role": "AXButton", "label": "Align Right"},
                {"path": "1/4/3", "role": "AXButton", "label": "Justify"},
            ]},
        ]},
        {"path": "2", "role": "AXScrollArea", "label": "Document body", "children": [
            {"path": "2/0", "role": "AXTextArea", "label": "Body text"},
        ]},
    ],
}

SCENARIOS = [
    # Deliberately worded differently from the labels, so a match requires
    # understanding rather than string search.
    Scenario("dialog (5 nodes)", SAVE_DIALOG,
             "Close this document and throw away the changes.", "2"),
    Scenario("Finder (15 nodes)", FINDER_WINDOW,
             "Make a new folder here.", "0/6"),
    Scenario("Settings (23 nodes)", SETTINGS_BLUETOOTH,
             "Connect my AirPods.", "2/2/1"),
    Scenario("Pages (30 nodes)", PAGES_WINDOW,
             "Centre the selected text.", "1/4/1"),
]


PROMPT = """\
Below is the accessibility tree of a Mac application window, as JSON. Each \
element has a "path".

{tree}

Task: {task}

Which single element should be clicked? Reply with only its path, e.g. 0/3 — \
no explanation."""


def ask(scenario: Scenario, helper: str) -> str:
    """One turn against the helper, driving the tool protocol.

    The helper is built with tools, so the model may emit `tool_request` lines
    before answering. Those must be replied to or the pipe stalls and the first
    line gets mistaken for the result — which is exactly what an earlier
    version of this script did, producing a meaningless 50%.

    Tool requests are declined here on purpose: the tree is already in the
    prompt, so this measures *reading the tree*, not tool use.
    """
    prompt = PROMPT.format(tree=json.dumps(scenario.tree, indent=1),
                           task=scenario.task)
    process = subprocess.Popen(
        [helper, "generate", "--instructions",
         "You read Mac accessibility trees and identify UI elements. "
         "The tree is given to you in the prompt — do not call any tools. "
         "Answer with a path only."],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    try:
        process.stdin.write(json.dumps({"prompt": prompt}) + "\n")
        process.stdin.flush()

        for _ in range(12):
            line = process.stdout.readline()
            if not line:
                return "(helper exited)"
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("type") == "tool_request":
                process.stdin.write(json.dumps({
                    "type": "tool_result",
                    "content": ("No tools are available. The accessibility tree "
                                "is in the prompt — answer from it."),
                }) + "\n")
                process.stdin.flush()
                continue
            return (message.get("content") or message.get("error") or "").strip()
        return "(no answer)"
    finally:
        process.kill()


def extract_path(answer: str) -> str | None:
    """Pull a path out of the reply, tolerating a little chattiness."""
    match = re.search(r"\b(\d+(?:/\d+)*)\b", answer)
    return match.group(1) if match else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=4)
    args = parser.parse_args()

    helper = local_engine.helper_path()
    if helper is None:
        print("macman-local helper not built — run `swift build` in helpers/")
        return 1

    print("Can Apple's on-device model navigate an Accessibility tree?\n")
    print(f"{'scenario':<22} {'nodes':>6} {'correct':>9}   answers")
    print("─" * 74)

    total_correct = total = 0
    for scenario in SCENARIOS:
        answers, correct = [], 0
        for _ in range(args.trials):
            reply = ask(scenario, str(helper))
            path = extract_path(reply)
            hit = path == scenario.expected
            correct += hit
            answers.append(path or reply[:12])
        total_correct += correct
        total += args.trials
        nodes = _count(scenario.tree)
        print(f"{scenario.name:<22} {nodes:>6} {correct:>4}/{args.trials}"
              f"      want={scenario.expected}  got={answers}")

    print("─" * 74)
    print(f"overall: {total_correct}/{total} "
          f"({100 * total_correct / max(1, total):.0f}%)\n")

    if total_correct / max(1, total) >= 0.8:
        print("→ Tree navigation looks viable on-device. §5 may be free.")
    elif total_correct / max(1, total) >= 0.4:
        print("→ Unreliable. Workable only for small trees, or with Claude.")
    else:
        print("→ Not viable on-device. §5 'operate any application' needs Claude.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

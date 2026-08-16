#!/usr/bin/env python3
"""Does asking for a *label* beat asking for a *path*?

Path construction is the measured weakness: 8/16 on tree navigation, with
failures like `0/4/1` for `1/4/1` (right leaf, wrong root) and `3/3` for `2` in
a flat tree (inventing nesting). The model identifies the element and then
mangles the notation.

That is the same shape as the shell-command failure, where the fix was to stop
making it author syntax. A path is syntax. A label is not.

Three formats, same trees and tasks:

    A  path     "reply with the path"            — baseline
    B  label    "reply with the element's label" — Python resolves label → path
    C  label+parent                              — disambiguates repeated labels

If B or C clearly beats A, addressing elements by label makes a large part of
Level 2 and 3 free (CAPABILITY.md §9).

Raw replies are written to `ax_label_raw.log` — the previous attempt at this
discarded them and was undiagnosable.

    .venv/bin/python tests/tasks/ax_label_vs_path.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from macman.engines import local as local_engine  # noqa: E402
from tests.tasks.ax_navigation import (  # noqa: E402
    FINDER_WINDOW, PAGES_WINDOW, SAVE_DIALOG, SETTINGS_BLUETOOTH,
)

RAW_LOG = Path(__file__).resolve().parent / "ax_label_raw.log"

CASES = [
    ("dialog", SAVE_DIALOG, "Close this document and throw away the changes.",
     "2", "Don't Save", None),
    ("Finder", FINDER_WINDOW, "Make a new folder here.",
     "0/6", "New Folder", None),
    # "Connect" appears twice — this is where a bare label is ambiguous and
    # format C should earn its keep.
    ("Settings", SETTINGS_BLUETOOTH, "Connect my AirPods.",
     "2/2/1", "Connect", "Nikhil's AirPods Pro"),
    ("Pages", PAGES_WINDOW, "Centre the selected text.",
     "1/4/1", "Center", "Alignment"),
]


def walk(node: dict, ancestors: tuple[str, ...] = ()):
    """Yield (path, role, label, ancestor_labels) for every labelled node."""
    label = node.get("label")
    if label:
        yield node["path"], node.get("role", ""), label, ancestors
    child_ancestors = ancestors + ((label,) if label else ())
    for child in node.get("children", ()):
        yield from walk(child, child_ancestors)


def resolve_label(tree: dict, label: str, parent: str | None = None) -> str | None:
    """Map a label back to a path — the step Python does so the model needn't.

    With a parent hint, only matches under an ancestor of that name count;
    this is what makes a repeated label like "Connect" unambiguous.
    """
    needle = label.strip().strip("\"'").casefold()
    matches = []
    for path, _role, node_label, ancestors in walk(tree):
        if node_label.casefold() != needle:
            continue
        if parent and not any(parent.casefold() in a.casefold() for a in ancestors):
            continue
        matches.append(path)
    return matches[0] if matches else None


PROMPTS = {
    "A path": (
        "Below is the accessibility tree of a Mac window, as JSON. Each element "
        'has a "path".\n\n{tree}\n\nTask: {task}\n\n'
        "Which element should be clicked? Reply with only its path, e.g. 0/3."
    ),
    "B label": (
        "Below is the accessibility tree of a Mac window, as JSON.\n\n{tree}\n\n"
        "Task: {task}\n\n"
        "Which element should be clicked? Reply with only its exact label text."
    ),
    "C label+parent": (
        "Below is the accessibility tree of a Mac window, as JSON.\n\n{tree}\n\n"
        "Task: {task}\n\n"
        "Which element should be clicked? Reply with exactly two lines:\n"
        "label: <the element's exact label>\n"
        "under: <the label of the group or item it belongs to, or - if none>"
    ),
}


def ask(helper: str, prompt: str) -> str:
    """One turn, answering any tool requests so the pipe never stalls."""
    process = subprocess.Popen(
        [helper, "generate", "--instructions",
         "You read Mac accessibility trees and identify UI elements. "
         "The tree is in the prompt — do not call tools. Answer in the exact "
         "format requested, with no explanation."],
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
                    "content": "No tools. Answer from the tree in the prompt.",
                }) + "\n")
                process.stdin.flush()
                continue
            return (message.get("content") or message.get("error") or "").strip()
        return "(no answer)"
    finally:
        process.kill()


def score(fmt: str, reply: str, tree: dict) -> str | None:
    """Turn a reply into a path, however the format asked for it."""
    if fmt == "A path":
        match = re.search(r"\b(\d+(?:/\d+)*)\b", reply)
        return match.group(1) if match else None

    if fmt == "B label":
        first = reply.strip().splitlines()[0] if reply.strip() else ""
        return resolve_label(tree, first)

    label = parent = None
    for line in reply.splitlines():
        if line.lower().startswith("label:"):
            label = line.split(":", 1)[1].strip()
        elif line.lower().startswith("under:"):
            parent = line.split(":", 1)[1].strip()
    if not label:
        return None
    return resolve_label(tree, label, None if parent in (None, "-", "") else parent)


def main() -> int:
    helper = local_engine.helper_path()
    if helper is None:
        print("macman-local not built — run `swift build` in helpers/")
        return 1

    trials = 3
    raw_lines: list[str] = []
    totals = {fmt: 0 for fmt in PROMPTS}
    count = 0

    print("Label vs path — can the model skip writing syntax?\n")
    print(f"{'case':<10} {'format':<16} {'score':>6}   resolved")
    print("─" * 70)

    for name, tree, task, want, _label, _parent in CASES:
        for fmt, template in PROMPTS.items():
            got = []
            for _ in range(trials):
                reply = ask(str(helper), template.format(
                    tree=json.dumps(tree, indent=1), task=task))
                raw_lines.append(f"=== {name} | {fmt}\n{reply}\n")
                got.append(score(fmt, reply, tree))
            hits = sum(path == want for path in got)
            totals[fmt] += hits
            print(f"{name:<10} {fmt:<16} {hits}/{trials}    want={want:<8} got={got}")
        count += trials
        print()

    RAW_LOG.write_text("\n".join(raw_lines), encoding="utf-8")

    print("─" * 70)
    for fmt in PROMPTS:
        pct = 100 * totals[fmt] / max(1, count)
        print(f"  {fmt:<16} {totals[fmt]:>2}/{count}  ({pct:.0f}%)")
    print(f"\nraw replies → {RAW_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

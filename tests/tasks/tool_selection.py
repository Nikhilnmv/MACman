#!/usr/bin/env python3
"""Does the on-device model pick the right tool, and does that hold as the
tool list grows?

Tool-selection confusion is the failure mode that scales badly. With 14 tools
the model already answered "are there documents open in Pages?" by calling
`app_info` and reporting "62 notes open in Pages" — a wrong tool producing a
confident, nonsensical answer. Adding tools makes that more likely, so it needs
a number before the list grows further.

**Nothing is executed.** Tool calls are intercepted and answered with a canned
result, so measuring "set the volume to 30" does not change the volume. This
measures *selection*, not effect.

    .venv/bin/python tests/tasks/tool_selection.py
    .venv/bin/python tests/tasks/tool_selection.py --trials 5
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import macman.engines.local as local_engine  # noqa: E402
from macman.agent.tools.actions import LOCAL_TOOLS  # noqa: E402

#: (task, expected tool). Each is phrased to map to exactly one tool; where
#: genuine overlap exists it is noted, because that ambiguity is itself a
#: finding rather than a flaw in the task.
CASES: list[tuple[str, str]] = [
    ("How many PDF files are in my Downloads folder?", "count_files"),
    ("How many files are in my Documents folder in total?", "count_files"),

    ("What files are in my Downloads folder?", "list_folder"),
    ("Show me the contents of my Documents folder.", "list_folder"),

    ("Find files with 'invoice' in the name in my Downloads folder.", "find_files"),
    ("Search my Documents folder for anything named 'report'.", "find_files"),

    ("Read the text of the file at /tmp/macman_bench.txt", "read_file"),

    ("What macOS version is this Mac running?", "system_info"),
    ("How much free disk space is there?", "system_info"),

    ("How many unread emails do I have?", "mail_control"),
    ("Draft an email to sam@example.com about lunch.", "mail_control"),

    ("What's on my calendar today?", "calendar_control"),
    ("Add a meeting called Standup at 2026-09-01T10:00.", "calendar_control"),

    ("How many notes do I have?", "notes_control"),
    ("Make a note called Shopping with milk and eggs.", "notes_control"),

    ("What reminders do I have outstanding?", "reminders_control"),
    ("Remind me to call the bank.", "reminders_control"),

    ("Open the Calculator app.", "open_app"),

    ("Set the volume to 30 percent.", "system_control"),
    ("Lock my Mac.", "system_control"),

    # Wi-Fi lives in system_control since the merge — `network_control` scored
    # 0/6 as a separate tool because people phrase Wi-Fi as a system setting.
    # Status questions route to system_info every time — measured 0/3 twice
    # for the alternative. Both tools now answer it; the benchmark follows the
    # model rather than insisting.
    ("Is Wi-Fi connected right now?", "system_info"),
    ("Turn Wi-Fi on.", "system_control"),

    ("Create a folder called Benchmark in /tmp", "file_operation"),
    ("Move /tmp/a.txt into /tmp/Benchmark", "file_operation"),

    ("What song is playing right now?", "media_control"),
    ("Pause the music.", "media_control"),

    ("What tabs do I have open in Safari?", "browser_control"),
    ("Open apple.com in my browser.", "browser_control"),

    ("Are there any documents open in Pages?", "document_control"),
    ("Export my resume at ~/Documents/resume.pages as a PDF.", "document_control"),

    ("What Shortcuts do I have on this Mac?", "run_shortcut"),

    ("Open my MACMan project in VS Code.", "vscode_control"),
    ("Open ~/code/app.py in the editor at line 42.", "vscode_control"),

    ("Fix the failing test in my nimoriz project.", "claude_code"),
    ("Ask Claude to explain the error in my project.", "claude_code"),
]

#: Canned results, so the model can finish its turn without anything running.
_STUBS = {
    "count_files": "There are exactly 25 PDF files in /Users/you/Downloads.",
    "list_folder": "12 items in /Users/you/Downloads:\nreport.pdf\nnotes.txt",
    "find_files": "1 file matching 'invoice' in /Users/you/Downloads:\ninvoice.pdf",
    "read_file": "/tmp/macman_bench.txt:\nhello world",
    "system_info": "macos_version: 26.3.1",
    "mail_control": "667 unread messages.",
    "calendar_control": "Nothing scheduled for today.",
    "notes_control": "You have 62 notes.",
    "reminders_control": "Nothing outstanding.",
    "open_app": "Opened Calculator.",
    "system_control": "Done.",
    "file_operation": "Created /tmp/Benchmark.",
    "media_control": "Nothing is playing in Music.",
    "browser_control": "No tabs are open in Safari.",
    "document_control": "No documents open in Pages.",
    "run_shortcut": "No shortcuts found.",
    "vscode_control": "Opened the project in VS Code.",
    "claude_code": "Claude Code finished the task.",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()

    tool_names = [tool.to_dict()["name"] for tool in LOCAL_TOOLS]
    print(f"Tool selection accuracy — {len(tool_names)} tools, "
          f"{len(CASES)} tasks, {args.trials} trials each")
    print("Nothing is executed; tool calls are stubbed.\n")

    chosen: list[str] = []

    def fake_serve(_self, request):
        name = request.get("name", "")
        chosen.append(name)
        return _STUBS.get(name, "Done.")

    original = local_engine.LocalEngine._serve_tool_call
    local_engine.LocalEngine._serve_tool_call = fake_serve

    per_tool: dict[str, list[int]] = {}
    confusions: Counter = Counter()

    try:
        for task, expected in CASES:
            hits = 0
            for _ in range(args.trials):
                before = len(chosen)
                try:
                    local_engine.LocalEngine().run(
                        task, session_id="bench", confirm=lambda *_: True)
                except Exception:
                    pass  # a failed turn counts as a miss, not a crash
                calls = chosen[before:]
                first = calls[0] if calls else None
                if first == expected:
                    hits += 1
                elif first:
                    confusions[f"{expected} → {first}"] += 1
                else:
                    confusions[f"{expected} → (no tool called)"] += 1
            per_tool.setdefault(expected, []).append(hits)
            mark = "ok  " if hits == args.trials else "MISS"
            print(f"  [{mark}] {hits}/{args.trials}  {expected:<17} {task[:48]}")
    finally:
        local_engine.LocalEngine._serve_tool_call = original

    print(f"\n{'─' * 66}\nPer tool")
    total = correct = 0
    for name in tool_names:
        scores = per_tool.get(name)
        if not scores:
            continue
        got, out_of = sum(scores), len(scores) * args.trials
        total += out_of
        correct += got
        bar = "█" * round(10 * got / out_of) + "░" * (10 - round(10 * got / out_of))
        print(f"  {name:<18} {bar} {got:>2}/{out_of}")

    print(f"\n  OVERALL {correct}/{total} ({100 * correct / max(1, total):.0f}%)")

    if confusions:
        print(f"\n{'─' * 66}\nMost common confusions")
        for pair, count in confusions.most_common(8):
            print(f"  {count}×  {pair}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

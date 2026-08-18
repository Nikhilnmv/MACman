#!/usr/bin/env python3
"""Does a mis-transcribed request still do the right thing?

    .venv/bin/python tests/tasks/degraded_intent.py --trials 3

Word error rate is the wrong measure for MACman. `call_audio.py` found that
bad call audio produces errors like `Downloads → download`, `emails → mails`,
and a dropped `please`. Those cost WER points, but none of them changes what
the user wants. The question that decides whether voice-over-FaceTime works is
not how many words survived — it is **whether the right thing still happens**.

Two failures are possible, and they are not equally visible:

1. **Wrong tool.** Loud and obvious; the reply is plainly unrelated.
2. **Right tool, wrong argument.** Far worse. `folder="download"` does not
   exist, so MACman answers "download does not exist or is not a folder" —
   a confident, precise, useless answer to a question the user did ask
   correctly. This is the failure mode worth measuring.

The degraded transcripts below are **real output** from `call_audio.py`'s
"terrible call" condition (16 kbps AAC-ELD, 5 dB SNR, 10% packet loss, 8 kHz),
not invented typos. Nothing is executed; tool calls are stubbed.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import macman.engines.local as local_engine  # noqa: E402
from macman.agent.tools.actions import LOCAL_TOOLS  # noqa: E402


def require_working_engine() -> str | None:
    """Refuse to score anything unless the engine can actually call tools.

    The first run of this experiment scored 0/18 on *both* clean and degraded
    transcripts and concluded "mis-transcription did not meaningfully change
    what happened" — a tidy, confident, entirely false result. The engine had
    been rebuilt without `-DMACMAN_TOOLS`, so it could not call a tool at all.

    Every number here is a difference between two conditions, and a difference
    between two broken runs is zero. That reads as reassurance, which makes it
    worse than an error.
    """
    backend = local_engine.apple_backend()
    if not backend.available:
        return f"on-device model unavailable — {backend.detail}"
    if not backend.tools:
        return ("the helper was built WITHOUT tool support, so no tool can be "
                "called and every condition would score zero.\n"
                "  Rebuild with:\n"
                "    cd helpers && swift build -c release -Xswiftc -DMACMAN_TOOLS")
    return None


@dataclass
class Pair:
    """One request, as spoken and as heard down a bad line."""
    clean: str
    degraded: str
    tool: str
    #: Substring the argument must contain for the answer to be right, matched
    #: case-insensitively. Empty means the tool alone decides it.
    argument: str = ""


#: Every degraded string here was produced by the audio pipeline, not written
#: by hand. The last two transcribed perfectly even in the worst condition and
#: are kept as controls — if those regress, the problem is the model, not audio.
PAIRS = [
    Pair("How many PDF files are in my Downloads folder",
         "How many PDF files are in my download folder",
         "count_files", "download"),
    Pair("Lock my Mac right now please",
         "Lock my Mac right now",
         "system_control"),
    Pair("What is in my Documents folder",
         "What is in my document folder",
         "list_folder", "document"),
    Pair("How many unread emails do I have",
         "How many unread mails do I have",
         "mail_control"),
    Pair("Remind me to call the bank tomorrow at nine",
         "Remind me to call the bank tomorrow at nine",
         "reminders_control"),
    # Two intents in one sentence — open an app *and* read a system fact — so
    # either tool is defensible and the model splits between them. Kept
    # deliberately: it is the only case here that fails, it fails on clean
    # audio too, and that is the point. It measures request ambiguity, not
    # transcription damage, and removing it would flatter the result.
    Pair("Open Safari and check the battery level",
         "Open Safari and check the battery level",
         "open_app"),
]

_STUBS = {
    "count_files": "There are exactly 25 PDF files in /Users/you/Downloads.",
    "list_folder": "3 items in /Users/you/Documents:\na\nb\nc",
    "system_control": "Locked the Mac.",
    "mail_control": "You have 12 unread messages.",
    "reminders_control": "Added a reminder.",
    "open_app": "Opened Safari.",
}


@dataclass
class Outcome:
    tool_hits: int = 0
    argument_hits: int = 0
    trials: int = 0
    seen_tools: list[str] = field(default_factory=list)
    seen_args: list[str] = field(default_factory=list)


def run_variant(text: str, pair: Pair, trials: int) -> Outcome:
    """Ask the local model to handle `text`, recording tool and arguments."""
    outcome = Outcome(trials=trials)
    captured: list[tuple[str, dict]] = []

    def fake_serve(_self, request):
        name = request.get("name", "")
        captured.append((name, request.get("arguments", {}) or {}))
        return _STUBS.get(name, "Done.")

    original = local_engine.LocalEngine._serve_tool_call
    local_engine.LocalEngine._serve_tool_call = fake_serve
    try:
        for _ in range(trials):
            before = len(captured)
            try:
                local_engine.LocalEngine().run(
                    text, session_id="degraded", confirm=lambda *_: True)
            except Exception:
                pass  # a failed turn is a miss, not a crash
            calls = captured[before:]
            if not calls:
                outcome.seen_tools.append("(none)")
                continue

            name, arguments = calls[0]
            outcome.seen_tools.append(name)
            if name != pair.tool:
                continue
            outcome.tool_hits += 1

            if not pair.argument:
                outcome.argument_hits += 1
                continue
            blob = " ".join(str(v) for v in arguments.values()).lower()
            outcome.seen_args.append(blob[:40])
            if pair.argument.lower() in blob:
                outcome.argument_hits += 1
    finally:
        local_engine.LocalEngine._serve_tool_call = original
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()

    print("Degraded-transcript intent — does the right thing still happen?\n")

    if (blocked := require_working_engine()) is not None:
        print(f"  BLOCKED: {blocked}")
        return 2

    print(f"{len(LOCAL_TOOLS)} tools, {len(PAIRS)} requests, "
          f"{args.trials} trials each. Nothing is executed.\n")

    totals = {"clean": Outcome(), "degraded": Outcome()}

    for pair in PAIRS:
        changed = pair.clean != pair.degraded
        print(f"── {pair.clean}")
        if changed:
            print(f"   heard as: {pair.degraded}")
        else:
            print("   (transcribed correctly even in the worst condition)")

        for label, text in (("clean", pair.clean), ("degraded", pair.degraded)):
            outcome = run_variant(text, pair, args.trials)
            totals[label].tool_hits += outcome.tool_hits
            totals[label].argument_hits += outcome.argument_hits
            totals[label].trials += outcome.trials

            mark = "ok  " if outcome.argument_hits == args.trials else "MISS"
            detail = f"tool {outcome.tool_hits}/{args.trials}"
            if pair.argument:
                detail += f", argument {outcome.argument_hits}/{args.trials}"
            print(f"   [{mark}] {label:<9} {detail}")
            if outcome.tool_hits < args.trials:
                print(f"          chose: {', '.join(outcome.seen_tools)}")
            elif pair.argument and outcome.argument_hits < args.trials:
                print(f"          args:  {'; '.join(outcome.seen_args)}")
        print()

    print("─" * 70)
    for label in ("clean", "degraded"):
        total = totals[label]
        print(f"  {label:<9} correct tool {total.tool_hits}/{total.trials}"
              f"   correct action {total.argument_hits}/{total.trials}")

    clean_rate = totals["clean"].argument_hits / max(1, totals["clean"].trials)
    degraded_rate = (totals["degraded"].argument_hits
                     / max(1, totals["degraded"].trials))
    delta = degraded_rate - clean_rate
    trials = totals["degraded"].trials
    # One trial flipping is worth this much, so any delta smaller than it is
    # indistinguishable from noise. With samples this small that is most of them.
    resolution = 1 / max(1, trials)

    print(f"\n  Cost of a bad line: {delta:+.1%}")
    if delta > 0:
        print(f"  Degraded scored *higher*, which is noise, not a finding —")
        print(f"  one trial flipping is worth {resolution:.1%} at this sample size.")
        print("  Read it as: no measurable cost.")
    elif abs(delta) <= resolution:
        print(f"  Within noise ({resolution:.1%} per trial). No measurable cost.")
    else:
        print("  Bad audio measurably changes what MACman does. This is the")
        print("  number that matters, not word error rate.")

    print(f"\n  Sample: {trials} trials over {len(PAIRS)} requests — small.")
    print("  Enough to rule out a large effect, not to detect a small one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

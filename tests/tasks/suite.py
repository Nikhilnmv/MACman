#!/usr/bin/env python3
"""Regression suite — the safety net for changes to routing and the engines.

Two modes, because the more valuable one costs nothing:

    # Routing only. No engine, no API key, no Ollama, free and instant.
    .venv/bin/python tests/tasks/suite.py --routing

    # Actually run the tasks against an engine.
    .venv/bin/python tests/tasks/suite.py --run local
    .venv/bin/python tests/tasks/suite.py --run cloud

**`--routing` is the one that matters most.** Every routing mistake is either a
privacy failure (a private task reaching Claude) or a capability failure (a
coding task stuck on a 7B local model). Those are the bugs worth catching on
every change, and they are catchable without spending anything — which is why
this mode exists separately.

Each case declares the engine it must reach. A `--run` pass additionally checks
the answer contains something it could only know by actually doing the work,
rather than trusting that a reply arrived.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from macman.config import Engine  # noqa: E402
from macman.router import route  # noqa: E402


@dataclass(frozen=True)
class Case:
    task: str
    expect: Engine
    #: Frontmost app, where a realistic request would carry that context.
    frontmost: str | None = None
    #: Substring the answer must contain for `--run` to count it a pass.
    #: None means "routing only" — no meaningful automated check on the answer.
    expect_in_answer: str | None = None
    #: Why this case exists, printed on failure so a regression explains itself.
    note: str = ""


# --------------------------------------------------------------------------- #
# Set A — private. These must NEVER route to the cloud.
# --------------------------------------------------------------------------- #

PRIVATE_CASES: list[Case] = [
    Case("what's in my Downloads folder", Engine.LOCAL,
         note="possessive + personal folder"),
    Case("summarise the contract in ~/Documents/lease.pages", Engine.LOCAL,
         note="private path outranks everything"),
    Case("open Numbers and total the Q3 column", Engine.LOCAL,
         note="private app by name"),
    Case("how many events are in Calendar today", Engine.LOCAL,
         note="private app by name"),
    Case("read ~/Desktop/todo.txt", Engine.LOCAL,
         note="private path"),
    Case("find my tax documents from last year", Engine.LOCAL,
         note="inherently personal noun"),
    Case("draft a reply to my email from Sarah", Engine.LOCAL,
         note="possessive + personal content"),
    Case("count the notes in my Notes app", Engine.LOCAL,
         note="private app"),
    Case("copy the figures from my spreadsheet into VS Code", Engine.LOCAL,
         note="STICKINESS: private signal must beat the developer app"),
    Case("what's my unread mail count", Engine.LOCAL,
         note="possessive + personal content"),
    Case("summarise my meeting notes", Engine.LOCAL,
         note="inherently personal noun"),
    Case("show me my contacts named John", Engine.LOCAL,
         note="private app + possessive"),
]

# --------------------------------------------------------------------------- #
# Set B — developer. Cloud is correct; landing on a 7B model is a capability bug.
# --------------------------------------------------------------------------- #

DEVELOPER_CASES: list[Case] = [
    Case("fix the CUDA out of memory error in VS Code", Engine.CLOUD,
         note="developer app by name — the original demo's task"),
    Case("run the test suite and fix what fails", Engine.CLOUD,
         frontmost="Terminal", note="frontmost supplies the context"),
    Case("what version of node is installed", Engine.CLOUD,
         frontmost="Terminal", note="frontmost supplies the context"),
    Case("git status and summarise what changed", Engine.CLOUD,
         frontmost="Ghostty", note="frontmost supplies the context"),
    Case("explain this stack trace in VS Code", Engine.CLOUD,
         note="developer app by name"),
    Case("document this function with a docstring", Engine.CLOUD,
         frontmost="Code",
         note="NEGATIVE: 'document' must not trigger the private noun rule"),
    Case("fix the numbers in this python script", Engine.CLOUD,
         frontmost="Code",
         note="NEGATIVE: lowercase 'numbers' must not match the Numbers app"),
    Case("check the build output in Xcode", Engine.CLOUD,
         note="developer app by name"),
]

# --------------------------------------------------------------------------- #
# Execution cases — only used by `--run`, where an answer can be checked.
# --------------------------------------------------------------------------- #

EXECUTION_CASES: list[Case] = [
    Case("What macOS version is this Mac running? Answer with just the number.",
         Engine.LOCAL, expect_in_answer="26",
         note="tier 1 shell: sw_vers"),
    Case("How many files are directly inside /etc? Answer with just the number.",
         Engine.LOCAL, expect_in_answer="",
         note="tier 1 shell: counting"),
    Case("What is the hostname of this Mac?", Engine.LOCAL, expect_in_answer="",
         note="tier 1 shell: system query"),
]


def _check_routing(cases: list[Case]) -> tuple[int, list[str]]:
    failures = []
    for case in cases:
        decision = route(case.task, frontmost_app=case.frontmost)
        ok = decision.engine is case.expect
        mark = "pass" if ok else "FAIL"
        context = f" [{case.frontmost}]" if case.frontmost else ""
        print(f"  {mark}  {decision.engine.value:<6} via {decision.rule:<10} "
              f"| {case.task[:46]}{context}")
        if not ok:
            failures.append(
                f"{case.task!r} → {decision.engine.value} "
                f"(expected {case.expect.value}) — {case.note}"
            )
    return len(cases) - len(failures), failures


def run_routing() -> int:
    print("Routing regression — no engine, no cost\n")

    print("Set A — private (must never reach the cloud)")
    private_passed, private_failures = _check_routing(PRIVATE_CASES)

    print("\nSet B — developer (must reach the cloud)")
    dev_passed, dev_failures = _check_routing(DEVELOPER_CASES)

    total = len(PRIVATE_CASES) + len(DEVELOPER_CASES)
    passed = private_passed + dev_passed

    print(f"\n{'─' * 64}")
    print(f"  {passed}/{total} routed correctly")

    # A private-side failure is categorically worse than a developer-side one:
    # one leaks data, the other only degrades capability. Report them apart.
    if private_failures:
        print(f"\n  ⚠️  {len(private_failures)} PRIVACY FAILURE(S) — a private "
              f"task would reach Claude:")
        for failure in private_failures:
            print(f"    • {failure}")
    if dev_failures:
        print(f"\n  {len(dev_failures)} capability failure(s) — a coding task "
              f"would run on the local model:")
        for failure in dev_failures:
            print(f"    • {failure}")

    return 0 if passed == total else 1


def run_execution(engine_name: str) -> int:
    """Actually run tasks and check the answers.

    Requires a working engine: Ollama pulled, or a funded Anthropic key.
    """
    print(f"Execution suite — {engine_name} engine\n")

    if engine_name == "local":
        from macman.engines.local import LocalEngine, LocalEngineUnavailable

        engine = LocalEngine()
        runner: Callable[[str], str] = lambda task: engine.run(
            task, session_id="suite", confirm=lambda *_: False
        )
        unavailable = LocalEngineUnavailable
    else:
        from macman.engines.cloud import CloudEngine

        engine = CloudEngine()
        runner = lambda task: engine.run(
            task, session_id="suite", confirm=lambda *_: False
        ).text
        unavailable = RuntimeError

    passed = 0
    for case in EXECUTION_CASES:
        started = time.monotonic()
        try:
            answer = runner(case.task)
        except unavailable as exc:
            print(f"  SKIP  {case.task[:44]}\n        {str(exc)[:120]}")
            continue
        except Exception as exc:  # a broken case must not abort the suite
            print(f"  FAIL  {case.task[:44]}\n        {type(exc).__name__}: {exc}")
            continue

        elapsed = time.monotonic() - started
        ok = (case.expect_in_answer or "") in answer
        passed += ok
        print(f"  {'pass' if ok else 'FAIL'}  ({elapsed:.1f}s) {case.task[:44]}")
        print(f"        → {answer.strip()[:100]}")

    print(f"\n{'─' * 64}\n  {passed}/{len(EXECUTION_CASES)} executed correctly")
    return 0 if passed == len(EXECUTION_CASES) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routing", action="store_true",
                        help="check routing only — free, needs no engine")
    parser.add_argument("--run", choices=["local", "cloud"],
                        help="actually execute tasks against an engine")
    args = parser.parse_args()

    if args.run:
        return run_execution(args.run)
    return run_routing()


if __name__ == "__main__":
    sys.exit(main())

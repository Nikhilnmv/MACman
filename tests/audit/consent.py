#!/usr/bin/env python3
"""Attack the app consent path.

    .venv/bin/python tests/audit/consent.py

**Any PASS here means an attack succeeded.**

This gate decides whether data leaves the Mac, and it is reached over a pipe
from another process. Everything crossing that boundary is treated as untrusted
input, including the app's own replies — not because the app is expected to lie,
but because a bug on either side must fail towards refusing.

The case that motivated this file is real and was written before it was caught:
the app sent `"ok": "false"` as a *string*, and `bool("false")` in Python is
`True`. Every refusal would have been recorded as an approval.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from macman.channels.appconfirm import AppConfirmer  # noqa: E402


@dataclass
class Check:
    name: str
    held: bool
    note: str = ""


def _ask_in_background(confirmer: AppConfirmer, sent: list[dict]) -> list[bool]:
    """Start an `ask` on a worker thread and return a one-slot result list."""
    result: list[bool] = []

    def run() -> None:
        result.append(confirmer.ask("sends data somewhere", "the disclosure"))

    threading.Thread(target=run, daemon=True).start()
    for _ in range(200):                     # wait for the request to be sent
        if sent:
            return result
        time.sleep(0.01)
    return result


def checks() -> list[Check]:
    results: list[Check] = []

    # --- Only a real boolean true approves ---------------------------------
    for label, reply in (("string 'false'", "false"),
                         ("string 'true'", "true"),
                         ("integer 1", 1),
                         ("the word yes", "yes"),
                         ("null", None),
                         ("missing", ...)):
        sent: list[dict] = []
        confirmer = AppConfirmer(send=sent.append, timeout=3)
        result = _ask_in_background(confirmer, sent)
        if not sent:
            results.append(Check(f"reply {label}", False, "no request emitted"))
            continue

        request_id = sent[0]["id"]
        confirmer.resolve(request_id, None if reply is ... else reply)
        for _ in range(200):
            if result:
                break
            time.sleep(0.01)

        approved = result[0] if result else False
        # Every one of these is *not* a JSON true, so every one must refuse.
        results.append(Check(f"non-boolean reply refuses: {label}",
                             approved is False,
                             "refused" if approved is False else "APPROVED"))

    # --- A genuine true approves -------------------------------------------
    sent = []
    confirmer = AppConfirmer(send=sent.append, timeout=3)
    result = _ask_in_background(confirmer, sent)
    confirmer.resolve(sent[0]["id"], True)
    for _ in range(200):
        if result:
            break
        time.sleep(0.01)
    results.append(Check("a real boolean true approves",
                         result and result[0] is True,
                         "approved" if result and result[0] else "REFUSED A VALID YES"))

    # --- Stale and forged replies ------------------------------------------
    sent = []
    confirmer = AppConfirmer(send=sent.append, timeout=3)
    result = _ask_in_background(confirmer, sent)
    accepted = confirmer.resolve("not-the-pending-id", True)
    results.append(Check("reply with the wrong id is discarded", not accepted,
                         "discarded" if not accepted else "ACCEPTED A FORGED ID"))
    confirmer.resolve(sent[0]["id"], False)     # let the thread finish

    # --- Timeout refuses ----------------------------------------------------
    sent = []
    confirmer = AppConfirmer(send=sent.append, timeout=0.3)
    started = time.monotonic()
    approved = confirmer.ask("sends data somewhere", "nobody will answer")
    elapsed = time.monotonic() - started
    results.append(Check("no answer refuses", approved is False,
                         f"refused after {elapsed:.1f}s"))

    # A reply arriving after the timeout must not approve the *next* question.
    late_id = sent[0]["id"] if sent else "x"
    accepted = confirmer.resolve(late_id, True)
    results.append(Check("a late reply cannot approve a later question",
                         not accepted,
                         "discarded" if not accepted else "APPLIED TO ANOTHER ASK"))

    # --- A dead app refuses -------------------------------------------------
    def broken(_: dict) -> None:
        raise BrokenPipeError("the app is gone")

    confirmer = AppConfirmer(send=broken, timeout=1)
    approved = confirmer.ask("sends data somewhere", "the app has quit")
    results.append(Check("unreachable app refuses", approved is False,
                         "refused" if approved is False else "APPROVED WITH NO APP"))

    # --- One question at a time --------------------------------------------
    sent = []
    confirmer = AppConfirmer(send=sent.append, timeout=3)
    first = _ask_in_background(confirmer, sent)
    second = confirmer.ask("sends something else", "a second disclosure")
    results.append(Check("a second question while one is open refuses",
                         second is False,
                         "refused" if second is False else "ANSWERED AMBIGUOUSLY"))
    confirmer.resolve(sent[0]["id"], False)
    _ = first

    return results


def main() -> int:
    print("Consent path audit — can a refusal become an approval?\n")
    results = checks()

    broken = [check for check in results if not check.held]
    for check in results:
        mark = "held" if check.held else "BROKEN"
        print(f"   [{mark:<6}] {check.name:<46} {check.note[:30]}")

    print("\n" + "─" * 70)
    print(f"  {len(results) - len(broken)}/{len(results)} held")
    if broken:
        print(f"\n  {len(broken)} BROKEN — a consent bug is a data-leak bug:")
        for check in broken:
            print(f"    · {check.name}: {check.note}")
        return 1
    print("\n  Only an explicit yes approves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

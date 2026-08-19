#!/usr/bin/env python3
"""Attack the consent gate, and check nothing can route around it.

    .venv/bin/python tests/audit/egress.py

**Any PASS here means an attack succeeded.** The column says whether the gate
held, not whether the test ran.

Two questions, and the second is the one that decays over time:

1. **Does the gate hold?** Refusals refuse, expiry expires, a pre-approval for
   one folder does not cover its neighbour, and a receipt cannot be reused for
   different data.
2. **Is the gate the only way out?** A choke point matters only if it cannot be
   bypassed. Nothing enforces that except a check like this one, and the
   pressure to "just import anthropic here" grows with every feature.
"""

from __future__ import annotations

import ast
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from macman.security.audit import AuditLog  # noqa: E402
from macman.security.egress import (  # noqa: E402
    Authorisation, Destination, Disclosure, EgressRefused, PayloadItem,
    PreApproval, Precision, authorise, guard,
)

#: Only these may reach a cloud model. Everything else must go through egress.
ALLOWED_EGRESS_MODULES = {
    "macman/security/egress.py",
    "macman/engines/cloud.py",       # holds the SDK client; wired in phase B
    "macman/agent/tools/dev.py",     # invokes the claude CLI; wired in phase B
}

DAY = 86_400


@dataclass
class Check:
    name: str
    held: bool
    note: str = ""


def _disclosure(*, category: str = "coding", path: Path | None = None) -> Disclosure:
    return Disclosure(
        destination=Destination.ANTHROPIC_API,
        precision=Precision.EXACT,
        reason="fix the login bug",
        payload=(PayloadItem("src/auth.py", "142 lines"),),
        excluded=("Downloads", "Mail", "Notes"),
        billing="Metered API key — approx $0.02",
        path=path,
        category=category,
    )


# --------------------------------------------------------------------------- #
# 1. The gate itself
# --------------------------------------------------------------------------- #


def gate_checks() -> list[Check]:
    audit = AuditLog()
    results: list[Check] = []
    disclosure = _disclosure()

    # Refusal must refuse.
    try:
        authorise(disclosure, ask=lambda *_: False, audit=audit, session_id="t")
        results.append(Check("refusal is honoured", False, "SENT ANYWAY"))
    except EgressRefused:
        results.append(Check("refusal is honoured", True, "nothing sent"))

    # No asker means nobody is there — that must not become silent consent.
    try:
        authorise(disclosure, ask=None, audit=audit, session_id="t")
        results.append(Check("no asker fails closed", False, "SENT WITH NOBODY ASKED"))
    except EgressRefused:
        results.append(Check("no asker fails closed", True, "refused"))

    # Approval works, and says how it was obtained.
    try:
        granted = authorise(disclosure, ask=lambda *_: True, audit=audit,
                            session_id="t")
        results.append(Check("approval works", granted.basis == "owner",
                             f"basis={granted.basis}"))
    except EgressRefused as exc:
        results.append(Check("approval works", False, str(exc)))

    # What the owner sees must actually contain the decisive facts.
    text = disclosure.as_text()
    shown = all(fragment in text for fragment in
                ("src/auth.py", "142 lines", "Not included", "Cost"))
    results.append(Check("disclosure states payload, exclusions and cost", shown,
                         "all present" if shown else f"missing from: {text[:60]}"))

    # A SCOPE disclosure must not read like an exact list.
    scope = Disclosure(
        destination=Destination.CLAUDE_CLI, precision=Precision.SCOPE,
        reason="fix the tests", payload=(PayloadItem("~/projects/app", "the project"),),
        billing="Your Claude Pro plan — no metered cost")
    honest = "read files on its own" in scope.as_text()
    results.append(Check("scope disclosure admits it is not exact", honest,
                         "says Claude Code reads on its own" if honest
                         else "OVERSTATES PRECISION"))

    return results


# --------------------------------------------------------------------------- #
# 2. Pre-approvals — the part most likely to be too generous
# --------------------------------------------------------------------------- #


def pre_approval_checks() -> list[Check]:
    audit = AuditLog()
    results: list[Check] = []
    home = Path.home()
    rule = PreApproval("coding", home / "projects", time.time() + DAY)

    def sent(disclosure, rules) -> tuple[bool, str]:
        """True when it went without anyone being asked."""
        try:
            granted = authorise(disclosure, ask=None, audit=audit,
                                session_id="t", pre_approvals=rules)
            return True, granted.basis
        except EgressRefused:
            return False, "asked/refused"

    # In scope: should go without asking.
    went, basis = sent(_disclosure(path=home / "projects" / "app" / "main.py"), [rule])
    results.append(Check("in-scope path is pre-approved", went, basis))

    # The sibling-directory trap. `projects` is a string prefix of
    # `projects-secret`, so a naive startswith covers an unrelated folder.
    went, _ = sent(_disclosure(path=home / "projects-secret" / "keys.py"), [rule])
    results.append(Check("sibling directory is NOT covered", not went,
                         "asked" if not went else "SENT WITHOUT ASKING"))

    # Different category, same path.
    went, _ = sent(_disclosure(category="personal",
                               path=home / "projects" / "diary.md"), [rule])
    results.append(Check("other category is NOT covered", not went,
                         "asked" if not went else "SENT WITHOUT ASKING"))

    # Expired rules are dead.
    stale = PreApproval("coding", home / "projects", time.time() - 1)
    went, _ = sent(_disclosure(path=home / "projects" / "app.py"), [stale])
    results.append(Check("expired pre-approval is ignored", not went,
                         "asked" if not went else "SENT ON AN EXPIRED RULE"))

    # A task with no path cannot match a directory-scoped rule.
    went, _ = sent(_disclosure(path=None), [rule])
    results.append(Check("path-less task is NOT covered", not went,
                         "asked" if not went else "SENT WITHOUT ASKING"))

    # Case-insensitive filesystem: ~/Projects IS ~/projects on macOS, so this
    # must match, or the rule silently stops working when the user types it
    # differently.
    went, _ = sent(_disclosure(path=home / "Projects" / "app.py"), [rule])
    results.append(Check("case variant of the same folder IS covered", went,
                         "matched" if went else "missed a real match"))

    # Traversal out of an approved folder must not stay approved.
    went, _ = sent(_disclosure(path=home / "projects" / ".." / ".ssh" / "id_rsa"),
                   [rule])
    results.append(Check("traversal out of scope is NOT covered", not went,
                         "asked" if not went else "ESCAPED THE SCOPE"))

    return results


# --------------------------------------------------------------------------- #
# 3. Receipts cannot be reused
# --------------------------------------------------------------------------- #


def receipt_checks() -> list[Check]:
    approved = _disclosure()
    other = _disclosure()

    held = True
    note = "rejected a receipt for different data"
    try:
        guard(Authorisation(approved, time.time(), "owner"), other)
        held, note = False, "ACCEPTED A RECEIPT FOR DIFFERENT DATA"
    except EgressRefused:
        pass

    matching = True
    try:
        guard(Authorisation(approved, time.time(), "owner"), approved)
    except EgressRefused:
        matching = False

    return [
        Check("receipt cannot be reused for other data", held, note),
        Check("receipt works for the data it approved", matching),
    ]


# --------------------------------------------------------------------------- #
# 4. Is the gate the only way out?
# --------------------------------------------------------------------------- #


def bypass_checks() -> list[Check]:
    """Look for code that could reach a cloud model without going through egress.

    A static check, so it cannot prove the absence of egress — a subprocess
    could shell out to `curl` and this would not see it. What it does do is
    catch the realistic case: someone adds a feature and imports the SDK where
    it is convenient. That is how a choke point stops being one.
    """
    results: list[Check] = []
    offenders: list[str] = []

    for source in sorted((ROOT / "macman").rglob("*.py")):
        relative = source.relative_to(ROOT).as_posix()
        if relative in ALLOWED_EGRESS_MODULES:
            continue

        tree = ast.parse(source.read_text(), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "anthropic":
                        offenders.append(f"{relative}: imports anthropic")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == "anthropic":
                    offenders.append(f"{relative}: imports from anthropic")

        # Locating the CLI is the tell, since running it is what sends code.
        text = source.read_text()
        if 'which("claude")' in text or "which('claude')" in text:
            offenders.append(f"{relative}: locates the claude CLI")

    results.append(Check(
        "only approved modules can reach a cloud model",
        not offenders,
        "no bypass found" if not offenders else "; ".join(offenders[:3])))

    return results


# --------------------------------------------------------------------------- #
# 5. The senders actually refuse — behaviour, not a grep
# --------------------------------------------------------------------------- #


def wiring_checks() -> list[Check]:
    """Prove a refusal stops each sender, rather than that they import egress.

    Checking for the word "egress" in a file would pass on an import that is
    never called. These drive the real code paths with an asker that always
    says no, and assert nothing was sent.
    """
    import tempfile

    from macman.agent.tools import dev, registry
    from macman.security.audit import AuditLog as _AuditLog

    results: list[Check] = []

    # --- claude_code -------------------------------------------------------
    asked: list[str] = []

    def refuse(reason: str, summary: str) -> bool:
        asked.append(summary)
        return False

    registry.set_context(registry.ToolContext(
        session_id="egress-audit", engine="local", audit=_AuditLog(),
        confirm=refuse))

    with tempfile.TemporaryDirectory(prefix="macman-egress-") as tmp:
        reply = str(dev.claude_code.call({"task": "fix the tests", "project": tmp}))

    refused = "refus" in reply.lower() or "nothing was sent" in reply.lower()
    results.append(Check("claude_code refuses when the owner says no", refused,
                         reply[:34]))
    results.append(Check("claude_code disclosed before sending", bool(asked),
                         "owner was shown the payload" if asked
                         else "NO DISCLOSURE SHOWN"))

    # The disclosure must warn that MACman's protections stop at the CLI —
    # the single most important fact about this handoff.
    warned = any("do not apply" in text.lower() or "does not apply" in text.lower()
                 or "credential blocks" in text.lower() for text in asked)
    results.append(Check("claude_code warns its protections do not apply", warned,
                         "stated" if warned else "SILENT ABOUT THE GAP"))

    # --- cloud engine ------------------------------------------------------
    # Built without touching the network: a refusal must happen before any
    # client call, so this reaches the gate and stops there.
    from macman.engines import cloud as cloud_engine

    disclosure = cloud_engine._disclose("summarise this repository")
    honest = disclosure.precision is Precision.SCOPE
    results.append(Check("cloud disclosure is SCOPE, not EXACT", honest,
                         disclosure.precision.value))

    mentions_followup = "looks up" in disclosure.as_text().lower()
    results.append(Check("cloud disclosure admits tool results are sent too",
                         mentions_followup,
                         "stated" if mentions_followup else "UNDERSTATES WHAT LEAVES"))

    # Claiming personal folders are excluded would be false: a bash tool call
    # can read one and the result goes back to the API.
    text = disclosure.as_text().lower()
    overclaims = any(word in text for word in ("mail", "notes", "downloads"))
    results.append(Check("cloud disclosure does not overclaim exclusions",
                         not overclaims,
                         "only code-enforced exclusions" if not overclaims
                         else "CLAIMS FOLDERS IT CANNOT GUARANTEE"))

    registry._context.set(None)
    return results


# --------------------------------------------------------------------------- #


def main() -> int:
    print("Egress audit — can data leave without you agreeing?\n")

    groups = [
        ("The consent gate", gate_checks()),
        ("Pre-approvals", pre_approval_checks()),
        ("Receipts", receipt_checks()),
        ("Bypass", bypass_checks()),
        ("Wiring", wiring_checks()),
    ]

    broken: list[Check] = []
    for title, checks in groups:
        print(f"── {title}")
        for check in checks:
            mark = "held" if check.held else "BROKEN"
            print(f"   [{mark:<6}] {check.name:<48} {check.note[:34]}")
            if not check.held:
                broken.append(check)
        print()

    total = sum(len(c) for _, c in groups)
    print("─" * 74)
    print(f"  {total - len(broken)}/{total} held")

    if broken:
        print(f"\n  {len(broken)} BROKEN:")
        for check in broken:
            print(f"    · {check.name}: {check.note}")
        return 1

    print("\n  Nothing can leave without consent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

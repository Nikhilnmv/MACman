#!/usr/bin/env python3
"""Attack the boundary between MACman.app and the daemon.

    .venv/bin/python tests/audit/consent.py

**Any PASS here means an attack succeeded.**

Two things cross this pipe that must not go wrong: the answer to "may this
leave?", and the settings that decide what MACman will do. Secrets travel one
way only — in.

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


def secret_checks() -> list[Check]:
    """Nothing secret may travel back toward the app.

    A settings pane able to display your API key is a settings pane able to
    leak it — into a screenshot, a screen recording, or a support thread. The
    same applies to the TOTP secret, which would let anyone mint session codes
    forever.
    """
    from macman import appsettings

    results: list[Check] = []
    payload = appsettings.read()
    flat = repr(payload)

    # Presence must be reported; the value must not be.
    results.append(Check("settings report whether a Claude key exists",
                         "cloud_key_configured" in payload,
                         "reported as a boolean"))
    results.append(Check("settings report whether a login code exists",
                         "totp_configured" in payload,
                         "reported as a boolean"))

    leaked = [name for name in ("sk-ant-", "ANTHROPIC_API_KEY") if name in flat]
    results.append(Check("no Claude key material in the settings payload",
                         not leaked,
                         "clean" if not leaked else f"LEAKED {leaked}"))

    # If a key is configured, prove its actual value is absent rather than
    # relying on the prefix check above.
    actual = appsettings.cloud_key()
    if actual:
        results.append(Check("the configured key's value is not in the payload",
                             actual not in flat,
                             "absent" if actual not in flat else "LEAKED THE KEY"))

    # The TOTP secret must never appear either.
    try:
        import keyring

        from macman import config

        secret = keyring.get_password(config.KEYCHAIN_SERVICE, "totp")
    except Exception:                            # noqa: BLE001
        secret = None
    if secret:
        results.append(Check("the login secret is not in the payload",
                             secret not in flat,
                             "absent" if secret not in flat else "LEAKED THE SECRET"))

    # Provisioning is the one action that returns a secret — a TOTP URI
    # contains it, which is what makes it scannable. That is unavoidable, so
    # what matters is that it goes nowhere persistent.
    #
    # Deliberately checked *without* calling provision(): doing so would mint a
    # new secret and invalidate whatever is in the user's authenticator app. A
    # test that breaks your login to prove your login is safe is not a test
    # worth having.
    from pathlib import Path

    from macman import config as macman_config

    log = Path(macman_config.AUDIT_LOG)
    logged = "otpauth://" in log.read_text(errors="replace") if log.exists() else False
    results.append(Check("no provisioning URI in the audit log", not logged,
                         "clean" if not logged else "LEAKED A TOTP SECRET"))

    config_file = Path(macman_config.userconfig.CONFIG_PATH)
    in_config = ("otpauth://" in config_file.read_text(errors="replace")
                 if config_file.exists() else False)
    results.append(Check("no provisioning URI in config.toml", not in_config,
                         "clean" if not in_config else "LEAKED A TOTP SECRET"))

    results.append(Check("settings never return a provisioning URI",
                         "otpauth" not in flat, "absent"))

    # Every credential the code stores must be reachable by both removal
    # paths. This check exists because it was not: the Claude key was added
    # when settings moved it into the Keychain, and "revoke everything" kept
    # deleting only the TOTP secret for weeks. Someone who ran it believing
    # they had turned MACman off would have left a working, billable
    # credential behind.
    #
    # Structural rather than a list, so the next credential someone adds fails
    # this check instead of being quietly forgotten.
    import re

    root = Path(macman_config.__file__).resolve().parents[1]
    services = set()
    for source in (root / "macman").rglob("*.py"):
        for match in re.finditer(r'"(com\.macman\.[a-z]+)"', source.read_text()):
            services.add(match.group(1))

    for script, label in ((root / "scripts/revoke_all.py", "revoke_all.py"),
                          (root / "scripts/uninstall.sh", "uninstall.sh")):
        text = script.read_text() if script.exists() else ""
        # revoke_all reaches the services through appsettings/config constants,
        # so accept either the literal or the constant that resolves to it.
        missing = [
            service for service in services
            if service not in text
            and not (service == "com.macman.totp" and "KEYCHAIN_SERVICE" in text)
            and not (service == "com.macman.cloud" and "clear_cloud_key" in text)
        ]
        results.append(Check(
            f"{label} covers every stored credential",
            not missing,
            f"{len(services)} known" if not missing else f"MISSES {missing}"))

    # The allowlist decides who can command this Mac, so losing an entry is a
    # security-relevant bug, not a UI annoyance — and losing *all* of them
    # silently turns MACman off in a way nothing reports.
    #
    # This existed: the settings window computed the new list itself and posted
    # it, so a window whose snapshot had not loaded yet wrote a list containing
    # only the handle just typed. It emptied a real allowlist. The operations
    # are now intent-based, and the daemon is the only thing that decides what
    # the list becomes.
    from macman import userconfig

    original = list(userconfig.load().get("allowed_handles", []))
    try:
        userconfig.update(allowed_handles=["+447700900001", "+447700900002"])
        appsettings.add_handle("+447700900003")
        after_add = userconfig.load()["allowed_handles"]
        results.append(Check(
            "adding a handle keeps the existing ones",
            len(after_add) == 3,
            f"{len(after_add)} handles" if len(after_add) == 3
            else f"LOST DATA: {after_add}"))

        appsettings.remove_handle("+447700900001")
        after_remove = userconfig.load()["allowed_handles"]
        results.append(Check(
            "removing one handle keeps the others",
            after_remove == ["+447700900002", "+447700900003"],
            f"{len(after_remove)} left" if len(after_remove) == 2
            else f"WRONG: {after_remove}"))

        # An invalid handle must not be able to corrupt a good list.
        try:
            appsettings.add_handle("not-a-handle")
            rejected = False
        except appsettings.SettingRejected:
            rejected = True
        preserved = userconfig.load()["allowed_handles"] == after_remove
        results.append(Check(
            "a rejected handle leaves the list untouched",
            rejected and preserved,
            "rejected, list intact" if rejected and preserved else "LIST DAMAGED"))
    finally:
        userconfig.update(allowed_handles=original)

    # Only known fields may be written, so a hostile or buggy app cannot
    # inject arbitrary keys into the config file.
    try:
        appsettings.set_field("anything_goes", "x")
        writable = False
    except appsettings.SettingRejected:
        writable = True
    results.append(Check("unknown settings fields are refused", writable,
                         "refused" if writable else "WROTE AN ARBITRARY FIELD"))

    return results


def main() -> int:
    print("App boundary audit — consent answers and settings\n")
    results = checks() + secret_checks()

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

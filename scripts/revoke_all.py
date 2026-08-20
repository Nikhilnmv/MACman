#!/usr/bin/env python3
"""Cut off MACman's access, without uninstalling it.

    .venv/bin/python scripts/revoke_all.py            # report what exists
    .venv/bin/python scripts/revoke_all.py --revoke   # actually revoke
    .venv/bin/python scripts/revoke_all.py --revoke --purge-audit

**For complete removal use `scripts/uninstall.sh` instead.** That one needs
nothing but macOS — no repository, no virtualenv — which matters because the
people most likely to want MACman gone are the ones who installed a release
and never had a checkout. This script is the softer action: revoke the
credentials and stop the process, leave the install in place.

What this does, in order:

1. Kills any running MACman process.
2. Removes a LaunchAgent if an old version left one. Current versions do not
   install one — MACman.app runs the daemon as its child, which is what keeps
   the permissions attached to MACman rather than to Terminal.
3. Deletes **both** credentials from the Keychain: the TOTP secret, and the
   Claude API key. Every issued code dies with the first, and the second is
   what would otherwise let a cloud request succeed after you thought you had
   turned everything off.
4. Reports which macOS permissions are still granted, with the exact pane for
   each, because **only you can revoke those** and no program should be able to.

The audit log is kept by default. It is the record of what MACman did, and
destroying it on the way out is the wrong default; `--purge-audit` is explicit.

Nothing here needs a network connection, and nothing about MACman exists outside
this machine — there is no account to close and no service to cancel.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macman import config  # noqa: E402
from macman.preflight import run_checks  # noqa: E402
from macman.security import auth  # noqa: E402

LAUNCH_AGENT = Path.home() / "Library/LaunchAgents/com.macman.agent.plist"


def _say(done: bool, message: str) -> None:
    print(f"  [{'done' if done else '    '}] {message}")


def kill_processes(revoke: bool) -> None:
    result = subprocess.run(["pgrep", "-f", "macman.main"], capture_output=True, text=True)
    pids = [p for p in result.stdout.split() if p]
    if not pids:
        _say(True, "no MACman process running")
        return
    if not revoke:
        _say(False, f"{len(pids)} MACman process(es) running: {' '.join(pids)}")
        return
    subprocess.run(["pkill", "-f", "macman.main"], check=False)
    _say(True, f"killed {len(pids)} MACman process(es)")


def remove_launch_agent(revoke: bool) -> None:
    if not LAUNCH_AGENT.exists():
        _say(True, "no LaunchAgent installed")
        return
    if not revoke:
        _say(False, f"LaunchAgent present at {LAUNCH_AGENT}")
        return
    subprocess.run(["launchctl", "unload", str(LAUNCH_AGENT)], check=False,
                   capture_output=True)
    LAUNCH_AGENT.unlink()
    _say(True, "LaunchAgent unloaded and removed")


def revoke_totp(revoke: bool) -> None:
    if not auth.is_configured():
        _say(True, "no TOTP secret stored")
        return
    if not revoke:
        _say(False, f"TOTP secret present in Keychain ({config.KEYCHAIN_SERVICE})")
        return
    auth.revoke()
    _say(True, "TOTP secret deleted — all issued codes are now invalid")


def revoke_cloud_key(revoke: bool) -> None:
    """Delete the Claude API key.

    Missing from this script until an audit of the uninstall path caught it:
    the key was added when settings moved it into the Keychain, and
    "revoke everything" kept deleting only the TOTP secret. Someone who ran
    this believing they had turned MACman off would have left a working,
    billable credential behind.
    """
    import keyring

    from macman import appsettings

    # Reported separately, because `cloud_key()` also falls back to
    # ANTHROPIC_API_KEY and saying "present in Keychain" when it is really in a
    # dotfile sends someone looking in the wrong place — and leaves them
    # believing a key was deleted that this script cannot reach.
    in_keychain = keyring.get_password(
        appsettings.CLOUD_KEYCHAIN_SERVICE, "anthropic-api-key") is not None
    in_environment = os.environ.get("ANTHROPIC_API_KEY") is not None

    if not in_keychain and not in_environment:
        _say(True, "no Claude API key stored")
        return
    if not revoke:
        if in_keychain:
            _say(False, "Claude API key present in Keychain "
                        f"({appsettings.CLOUD_KEYCHAIN_SERVICE})")
        if in_environment:
            _say(False, "ANTHROPIC_API_KEY is set in the environment — this "
                        "script cannot unset it; remove it from your shell "
                        "profile or .env")
        return

    if in_keychain:
        appsettings.clear_cloud_key()
    if in_environment:
        # Still resolvable means it is coming from ANTHROPIC_API_KEY in the
        # environment, which this script cannot reach into and unset.
        _say(True, "Keychain entry cleared, but ANTHROPIC_API_KEY is still set "
                   "in your environment — remove it from your shell profile "
                   "or .env, or the cloud engine will still work")
    else:
        _say(True, "Claude API key deleted")


def purge_audit(purge: bool) -> None:
    if not config.AUDIT_LOG.exists():
        _say(True, "no audit log")
        return
    size_kb = config.AUDIT_LOG.stat().st_size / 1024
    if not purge:
        _say(False, f"audit log kept at {config.AUDIT_LOG} ({size_kb:.0f} KB) "
                    f"— pass --purge-audit to delete")
        return
    config.AUDIT_LOG.unlink()
    _say(True, "audit log deleted")


def report_permissions() -> None:
    """List still-granted permissions.

    Deliberately reports rather than acts: TCC is revocable only by you, in
    System Settings. A program that could switch off its own oversight would
    be exactly the wrong design.
    """
    granted = [check for check in run_checks() if check.granted]
    if not granted:
        print("\n  No macOS permissions are currently granted to this process.")
        return

    print("\n  Still granted — revoke these yourself in System Settings:")
    for check in granted:
        print(f"    • {check.name}")
        if check.url:
            print(f"        open '{check.url}'")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revoke", action="store_true",
                        help="actually revoke; without it this only reports")
    parser.add_argument("--purge-audit", action="store_true",
                        help="also delete the audit log")
    args = parser.parse_args()

    mode = "REVOKING" if args.revoke else "DRY RUN — nothing will be changed"
    print(f"\nMACman revocation — {mode}\n")

    kill_processes(args.revoke)
    remove_launch_agent(args.revoke)
    revoke_totp(args.revoke)
    revoke_cloud_key(args.revoke)
    purge_audit(args.revoke and args.purge_audit)
    report_permissions()

    if args.revoke:
        print(f"\n  MACman is disabled. To remove it entirely:")
        print(f"    rm -rf {Path(__file__).resolve().parent.parent}")
    else:
        print("\n  Re-run with --revoke to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

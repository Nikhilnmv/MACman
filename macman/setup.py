"""Guided setup — one command from clone to working.

    macman setup

Before this existed, a new user had to create a venv, grant five permissions
found by reading a table, install Ollama, provision a credential, and edit a
Python source file to add their phone number. That last step alone loses most
people, and none of it was verified end to end — the credential failure earlier
in this project's own history went unnoticed until a live test.

So this walks each step, re-checks after you act rather than trusting that you
did, and finishes with a self-test that proves the whole thing works.

Safe to re-run. Every step detects what is already done and offers to skip it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from macman import preflight, userconfig
from macman.engines import local as local_engine
from macman.security import auth, lockstate

_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security"


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #


def _step(number: int, total: int, title: str) -> None:
    print(f"\n\033[1m[{number}/{total}] {title}\033[0m")
    print("─" * 62)


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"  {prompt}{suffix}: ").strip()
    return answer or default


def _confirm(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = input(f"  {prompt} [{hint}]: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #


def step_permissions() -> bool:
    """Walk the TCC grants, re-checking after the user acts.

    Grants attach to the *calling* binary, so this reports what the terminal
    running setup currently has — which is what MACman will have too.
    """
    while True:
        checks = preflight.run_checks()
        # Microphone and Speech Recognition are prompted at first use by the
        # voice helpers (v2); there's no read-only probe, so they always look
        # missing here and shouldn't block setup.
        required = [c for c in checks
                    if c.name in {"Accessibility", "Full Disk Access", "Screen Recording"}]
        missing = [c for c in required if not c.granted]

        for check in required:
            mark = "\033[32m✓\033[0m" if check.granted else "\033[33m•\033[0m"
            print(f"  {mark} {check.name:<20} {check.needed_for}")

        if not missing:
            print("\n  All required permissions granted.")
            return True

        print(f"\n  {len(missing)} still needed. MACman works without them, but:")
        for check in missing:
            print(f"    · without {check.name}, {check.needed_for} won't work")

        if not _confirm("\n  Open System Settings for the first one now?"):
            return False

        target = missing[0]
        if target.url:
            subprocess.run(["open", target.url], check=False)
        print(f"\n  Add this terminal under {target.name}, then come back.")
        input("  Press Enter once you've done it (or to skip)... ")
        print()


def step_allowlist() -> bool:
    settings = userconfig.load()
    existing = settings["allowed_handles"]

    if existing:
        print(f"  Currently allowed: {', '.join(existing)}")
        if not _confirm("  Change this?", default=False):
            return True

    print("  Which handle may command this Mac?")
    print("  Use the Apple ID or phone number you'll text FROM, in the exact")
    print("  form Messages stores it — e.g. +919876543210 or you@icloud.com")
    print("  Leave blank to allow nobody for now.\n")

    handle = _ask("Handle")
    if not handle:
        print("  Skipped — nobody is allowlisted, so MACman will ignore all messages.")
        return False

    userconfig.update(allowed_handles=[handle])
    print(f"  Saved. Only {handle} can reach MACman.")
    return True


def step_wake_phrase() -> bool:
    settings = userconfig.load()
    print(f"  Current wake phrases: {', '.join(settings['wake_phrases'])}")
    print("  Say one of these to open the door before authenticating.")

    if not _confirm("  Add your own?", default=False):
        return True

    phrase = _ask("New wake phrase (e.g. 'wake up macman')").lower()
    if phrase:
        userconfig.update(wake_phrases=sorted({*settings["wake_phrases"], phrase}))
        print(f"  Added. Saying \"{phrase}\" now wakes MACman.")
    return True


def step_local_engine() -> bool:
    """Apple's on-device model — already on this Mac, nothing to install."""
    backend = local_engine.apple_backend()

    if backend.available and backend.tools:
        print("  \033[32m✓\033[0m Apple on-device model ready, with tool support")
        return True

    if backend.available:
        print("  \033[32m✓\033[0m Apple on-device model ready — text only")
        print("\n  It can reason and summarise, but can't run commands or read")
        print("  files yet. Tool support needs a rebuild with full Xcode:")
        print("    sudo xcode-select -s /Applications/Xcode.app")
        print("    cd helpers && swift build -c release -Xswiftc -DMACMAN_TOOLS")
        return True  # usable as-is, so not a setup failure

    print(f"  \033[33m•\033[0m {backend.detail}")
    if local_engine.helper_path() is None:
        print("\n  Build the helper:\n")
        print("    cd helpers && swift build -c release")
    return False


def step_cloud_engine() -> bool:
    """The cloud half is optional — private tasks never touch it."""
    import os

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key.startswith("sk-ant-"):
        print("  \033[32m✓\033[0m Anthropic key present")
        return True

    print("  \033[33m•\033[0m No Anthropic key — the developer task set is unavailable.")
    print("    Private, on-device tasks work without it.")
    print("\n    To enable coding tasks, get a key from console.anthropic.com and:")
    print(f"      echo 'ANTHROPIC_API_KEY=sk-ant-...' >> "
          f"{Path(__file__).resolve().parent.parent / '.env'}")
    return False


def step_credential() -> bool:
    """Provision TOTP, verifying the authenticator actually received it."""
    if auth.is_configured():
        print("  \033[32m✓\033[0m Credential already provisioned.")
        if not _confirm("  Replace it? (invalidates your current authenticator entry)",
                        default=False):
            return True

    script = Path(__file__).resolve().parent.parent / "scripts" / "setup_totp.py"
    print("  Setting up your login code. This shows a QR to scan, then checks")
    print("  your app actually got it before continuing.\n")
    result = subprocess.run([sys.executable, str(script), "--force"])
    return result.returncode == 0


def step_self_test() -> bool:
    """Prove the pieces work together, rather than asserting they do."""
    state = lockstate.read()
    print(f"  Lock state: {state.tier.value} — {state.explain()}")

    settings = userconfig.load()
    ok = True

    if not settings["allowed_handles"]:
        print("  \033[33m•\033[0m No handle allowlisted — MACman will ignore all messages.")
        ok = False
    if not auth.is_configured():
        print("  \033[33m•\033[0m No credential — sessions can't start.")
        ok = False

    if local_engine.apple_backend().available:
        print("  Running a real task on the local engine...")
        try:
            answer = local_engine.LocalEngine().run(
                "What is the hostname of this Mac? Answer in one short sentence.",
                session_id="setup-selftest", confirm=lambda *_: False,
            )
            print(f"  \033[32m✓\033[0m {answer.strip()[:110]}")
        except Exception as exc:
            print(f"  \033[33m•\033[0m Local engine failed: {str(exc)[:110]}")
            ok = False
    else:
        print("  \033[33m•\033[0m Skipping engine test — no local backend.")
        ok = False

    return ok


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

STEPS = [
    ("macOS permissions", step_permissions),
    ("Who may command this Mac", step_allowlist),
    ("Wake phrase", step_wake_phrase),
    ("Local engine (private tasks, free)", step_local_engine),
    ("Cloud engine (coding tasks, optional)", step_cloud_engine),
    ("Your login credential", step_credential),
    ("Self-test", step_self_test),
]


def main() -> int:
    print("\n\033[1mMACman setup\033[0m")
    print("Everything is re-runnable, and nothing here is irreversible.")
    print(f"Settings are saved to {userconfig.CONFIG_PATH}")

    results: dict[str, bool] = {}
    for index, (title, step) in enumerate(STEPS, start=1):
        _step(index, len(STEPS), title)
        try:
            results[title] = step()
        except KeyboardInterrupt:
            print("\n\n  Setup interrupted. Re-run `macman setup` to continue.")
            return 1

    print(f"\n\033[1mSummary\033[0m\n{'─' * 62}")
    for title, ok in results.items():
        print(f"  {'\033[32m✓\033[0m' if ok else '\033[33m•\033[0m'} {title}")

    if all(results.values()):
        print("\n  Ready. Start MACman with:\n")
        print("    macman serve")
        print("\n  Then text your Mac your wake phrase, followed by a code.")
        return 0

    print("\n  Some steps are incomplete — see the notes above. MACman will still")
    print("  run whatever is ready. Re-run `macman setup` any time.")
    return 1

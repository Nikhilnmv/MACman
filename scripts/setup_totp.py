#!/usr/bin/env python3
"""Set up your MACman credential, with the loop actually closed.

    .venv/bin/python scripts/setup_totp.py          # set up if not already
    .venv/bin/python scripts/setup_totp.py --force  # replace an existing one

`macman auth provision` prints the URI and trusts you to get it into an
authenticator app. That failed silently once already: the secret landed in the
Keychain, nothing landed in the app, and the only symptom was every code being
rejected during a live test — with no way to tell that apart from a bug.

So this script does three things instead of one:

1. Generates the secret and stores it in the Keychain.
2. Renders a **scannable QR code in this terminal** — no copy-paste step.
3. **Verifies the app agrees** before exiting, by asking you for a code and
   checking it. If that check passes, the credential provably works.

The secret is never written to a file, and nothing here should be pasted into
a chat — only the "verified" line at the end is worth reporting.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import keyring  # noqa: E402
import pyotp  # noqa: E402
import qrcode  # noqa: E402

from macman import config  # noqa: E402
from macman.security import auth  # noqa: E402


def render_qr(uri: str) -> None:
    """Print the provisioning URI as an ASCII QR code.

    `border=1` and half-block characters keep it inside a normal terminal
    window; a default-sized QR wraps and becomes unscannable.
    """
    code = qrcode.QRCode(border=1)
    code.add_data(uri)
    code.make(fit=True)
    code.print_ascii(invert=True)


def confirm_app_matches(secret: str, attempts: int = 3) -> bool:
    """Ask for a code and check it against the stored secret.

    This is the step that would have caught the earlier failure immediately
    rather than during a live iMessage test.
    """
    totp = pyotp.TOTP(secret)
    for remaining in range(attempts, 0, -1):
        entered = input("  Enter the 6-digit code your app shows now: ").strip()
        digits = "".join(character for character in entered if character.isdigit())

        if len(digits) != 6:
            print("  That isn't six digits. Try again.\n")
            continue

        # valid_window=1 tolerates ±30s of clock drift between Mac and phone.
        if totp.verify(digits, valid_window=1):
            return True

        print(f"  No match — that code isn't from this secret. "
              f"{remaining - 1} attempt(s) left.")
        print("  Make sure you scanned the QR *above*, not an older MACman "
              "entry in your app.\n")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="replace an existing secret (invalidates the old one)")
    args = parser.parse_args()

    if auth.is_configured() and not args.force:
        print("A credential already exists.")
        print("If codes are being rejected, it does not match your app — "
              "re-run with --force to replace it.")
        return 1

    uri = auth.provision(force=True)
    secret = keyring.get_password(config.KEYCHAIN_SERVICE, "totp-secret")

    print("\nScan this with your authenticator app "
          "(Google Authenticator, Authy, 1Password, Raivo…):\n")
    render_qr(uri)
    print("  If the QR won't scan, add an account manually using this URI:")
    print(f"    {uri}\n")

    print("Now let's confirm your app actually has it.\n")
    if not confirm_app_matches(secret):
        print("\n  Could not verify. The credential is stored on this Mac but "
              "your app does not match it —")
        print("  MACman would reject every code. Re-run this script and scan "
              "the QR again.")
        return 1

    print("\n  ✅ Verified — your authenticator matches the credential on this Mac.")
    print("     Codes from this app will now work with MACman.")
    print(f"     Delete it any time with: "
          f".venv/bin/python -m macman.main auth revoke")
    return 0


if __name__ == "__main__":
    sys.exit(main())

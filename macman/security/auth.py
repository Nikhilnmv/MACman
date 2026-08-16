"""TOTP session authentication.

MACman's credential is deliberately **not** your Mac login password (DESIGN.md
§6.1–6.2). It is a standard RFC 6238 time-based code from an authenticator app
on your phone, which gives three properties the login password cannot:

* Codes expire in 30 seconds, so an old message history is worthless.
* A leak costs you MACman, not your Mac — and revocation is regenerating one
  secret, with nothing about the machine changing.
* The secret lives in the macOS Keychain, never in the repo, a config file,
  or `.env`.

Replay is closed explicitly: a code that authenticated one session cannot
authenticate another, even inside its 30-second validity window.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

import keyring
import pyotp

from macman import config

_KEYCHAIN_ACCOUNT = "totp-secret"

#: Accept one step either side of now, covering clock drift between the Mac and
#: your phone without meaningfully widening the window.
_VALID_WINDOW = 1


class AuthResult(str, Enum):
    OK = "ok"
    BAD_CODE = "bad_code"
    REPLAYED = "replayed"
    LOCKED_OUT = "locked_out"
    NOT_CONFIGURED = "not_configured"


# --------------------------------------------------------------------------- #
# Secret management
# --------------------------------------------------------------------------- #


def is_configured() -> bool:
    return keyring.get_password(config.KEYCHAIN_SERVICE, _KEYCHAIN_ACCOUNT) is not None


def provision(*, account_name: str = "MACman", force: bool = False) -> str:
    """Generate and store a new TOTP secret, returning a provisioning URI.

    Render the URI as a QR code and scan it with any authenticator app.
    Provisioning again invalidates every previously issued code — which is
    exactly the revocation path.

    Raises:
        RuntimeError: if a secret already exists and `force` is not set, so an
            accidental re-run cannot silently lock you out.
    """
    if is_configured() and not force:
        raise RuntimeError(
            "A TOTP secret already exists. Pass force=True to replace it — "
            "this invalidates the current authenticator entry."
        )

    secret = pyotp.random_base32()
    keyring.set_password(config.KEYCHAIN_SERVICE, _KEYCHAIN_ACCOUNT, secret)
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name="MACman")


def revoke() -> None:
    """Delete the stored secret. MACman refuses all sessions until re-provisioned."""
    try:
        keyring.delete_password(config.KEYCHAIN_SERVICE, _KEYCHAIN_ACCOUNT)
    except keyring.errors.PasswordDeleteError:
        pass


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #


@dataclass
class Authenticator:
    """Verifies codes and tracks failures and replays.

    One instance per MACman process; state is intentionally in memory, so a
    restart clears a lockout. That is an accepted trade: an attacker who can
    restart the process already has code execution on the machine.
    """

    #: Codes already used, so none is accepted twice inside its window.
    _spent: set[str] = field(default_factory=set)
    _failures: int = 0
    _locked_until: float = 0.0

    @property
    def locked_out(self) -> bool:
        return time.time() < self._locked_until

    def seconds_until_unlock(self) -> int:
        return max(0, int(self._locked_until - time.time()))

    def verify(self, code: str) -> AuthResult:
        """Check a submitted code.

        Args:
            code: The 6-digit code, as texted or spoken. Spaces are tolerated
                because voice transcription inserts them.
        """
        if self.locked_out:
            return AuthResult.LOCKED_OUT

        secret = keyring.get_password(config.KEYCHAIN_SERVICE, _KEYCHAIN_ACCOUNT)
        if secret is None:
            return AuthResult.NOT_CONFIGURED

        cleaned = "".join(character for character in code if character.isdigit())

        # Replay is checked before validity: a spent code must not count as a
        # failed attempt, or an attacker replaying one could trigger a lockout.
        if cleaned in self._spent:
            return AuthResult.REPLAYED

        if not pyotp.TOTP(secret).verify(cleaned, valid_window=_VALID_WINDOW):
            self._failures += 1
            if self._failures >= config.MAX_AUTH_FAILURES:
                self._locked_until = time.time() + config.AUTH_LOCKOUT_SECONDS
                self._failures = 0
            return AuthResult.BAD_CODE

        self._spent.add(cleaned)
        self._failures = 0
        return AuthResult.OK


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #


@dataclass
class Session:
    """An authenticated session, valid until idle for too long.

    Re-authenticating mid-conversation would be intolerable on a voice call, so
    activity extends the session rather than the clock expiring it outright.
    """

    session_id: str
    handle: str
    started_at: float
    last_active: float

    @property
    def expired(self) -> bool:
        return time.time() - self.last_active > config.SESSION_IDLE_TIMEOUT_SECONDS

    def touch(self) -> None:
        self.last_active = time.time()

    @classmethod
    def start(cls, handle: str) -> Session:
        now = time.time()
        return cls(session_id=uuid.uuid4().hex[:12], handle=handle,
                   started_at=now, last_active=now)

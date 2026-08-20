"""First-run setup, driven by MACman.app.

The wizard is the app's; the decisions are here, so the CLI (`macman setup`) and
the app cannot disagree about what "set up" means.

## The one time a secret travels outward

Everywhere else in this codebase, secrets go one way: in. `appsettings.read()`
reports whether a credential exists and never its value, and an audit enforces
that.

`provision()` is the deliberate exception. A TOTP provisioning URI *contains*
the secret — that is what makes it scannable — and there is no way to enrol an
authenticator without putting it on screen once. Every TOTP setup anywhere works
this way.

Three things keep that narrow:

* It is returned only from an **explicit, user-initiated** action, never from a
  passive read of settings.
* It is never written to the audit log or the config file.
* Provisioning again invalidates every previously issued code, so a URI that
  leaked can be revoked by re-running it.

## Why the self-test runs a real task

Setup could end by asserting that everything works. Instead it runs one genuine
local task and counts outbound sockets while doing it, so the install proves
MACman's central claim **on the user's own machine** rather than asking them to
believe a number in a document.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Any

from macman import config, userconfig
from macman.security import auth, permissions


@dataclass
class SelfTestResult:
    ok: bool
    task: str
    answer: str
    outbound: int
    elapsed_ms: int
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "task": self.task, "answer": self.answer,
            "outbound": self.outbound, "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


def status() -> dict[str, Any]:
    """What is done, and what is still needed to be usable.

    `complete` deliberately requires only the things without which MACman
    cannot function at all: someone allowed to reach it, and a way to
    authenticate them. Permissions are capabilities, not prerequisites — a
    user who declines Full Disk Access still has a working CLI and voice
    assistant, and telling them setup "failed" would be false.
    """
    settings = userconfig.load()
    handles = list(settings.get("allowed_handles", []))
    configured = auth.is_configured()

    return {
        "has_handles": bool(handles),
        "has_code": configured,
        "full_disk": permissions.PERMISSIONS["full_disk"].granted(),
        "complete": bool(handles) and configured,
        # The text channel is the one thing that genuinely cannot work without
        # a permission, so it is called out separately from "complete".
        "text_channel_ready": (bool(handles) and configured
                               and permissions.PERMISSIONS["full_disk"].granted()),
    }


def provision(force: bool = False) -> str:
    """Create a login code and return its provisioning URI, for a QR.

    See the module docstring: this is the one value in MACman that a UI is
    allowed to display, and only because enrolment is otherwise impossible.
    """
    return auth.provision(force=force)


def verify(code: str) -> bool:
    """Check a code against the freshly provisioned secret.

    Setup is not finished until one real code round-trips. Skipping this is how
    someone discovers their authenticator was misconfigured while standing in a
    car park unable to reach their Mac.
    """
    from macman.voice.digits import spoken_digits

    cleaned = spoken_digits(code) or code
    return auth.Authenticator().verify(cleaned) is auth.AuthResult.OK


def self_test() -> SelfTestResult:
    """Run one real local task, counting every outbound connection.

    The socket patch is the same technique `tests/audit/network.py` uses, and
    carries the same caveat: it sees this process only. The Swift helpers are
    separate processes and were checked separately — RELIABILITY.md has that.
    """
    task = "how many files are in my Downloads folder?"
    started = time.monotonic()

    outbound: list[str] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def watched_connect(self, address, *args, **kwargs):     # noqa: ANN001
        outbound.append(str(address))
        return real_connect(self, address, *args, **kwargs)

    def watched_connect_ex(self, address, *args, **kwargs):  # noqa: ANN001
        outbound.append(str(address))
        return real_connect_ex(self, address, *args, **kwargs)

    socket.socket.connect = watched_connect
    socket.socket.connect_ex = watched_connect_ex
    try:
        from macman.engines.local import LocalEngine

        answer = LocalEngine().run(task, session_id="setup-selftest",
                                   confirm=lambda *_: False)
        elapsed = int((time.monotonic() - started) * 1000)
        return SelfTestResult(ok=True, task=task, answer=str(answer)[:400],
                              outbound=len(outbound), elapsed_ms=elapsed)
    except Exception as exc:                                 # noqa: BLE001
        elapsed = int((time.monotonic() - started) * 1000)
        return SelfTestResult(
            ok=False, task=task, answer="", outbound=len(outbound),
            elapsed_ms=elapsed, error=f"{type(exc).__name__}: {exc}"[:200])
    finally:
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex

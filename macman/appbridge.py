"""The daemon's side of the conversation with MACman.app.

    macman bridge

Newline-delimited JSON over stdin and stdout. Not a socket, deliberately: the
app spawns this process and therefore already owns its pipes, so there is
nothing to bind, nothing to authenticate, and nothing for another process on
this Mac to connect to. The localhost design this replaced needed a token, an
origin check and a rebinding defence to reach the same place.

## Why the app must be the parent

macOS attributes a permission to the **responsible process** — the app that
launched the one asking. Run from Terminal, MACman's permissions belong to
Terminal, which means granting Full Disk Access to *every script the user will
ever run there*. Launched by `MACman.app`, they belong to MACman alone, and the
user can revoke them without crippling their shell.

That is the whole reason this file exists. It is also why the daemon must stay
a child: started by `launchd` instead, the responsible process becomes
`launchd`, permissions attach to a bare binary with no bundle, and the benefit
is gone.

## Protocol

Both directions are one JSON object per line, flushed immediately.

    app → bridge    {"type": "ping"}          request an immediate status
                    {"type": "reload"}        settings changed on disk
                    {"type": "shutdown"}      exit cleanly
                    {"type": "consent_result", "id": …, "ok": true}

    bridge → app    {"type": "status", …}     periodic, and after every command
                    {"type": "ready", …}      once, at startup
                    {"type": "consent", "id": …, "reason": …, "body": …}

Consent is asked over this pipe rather than in any UI the daemon draws itself,
because the app is the only surface a browser extension cannot read or click.
The asking thread parks on an `Event` while this loop keeps reading — see
`channels/appconfirm`.
"""

from __future__ import annotations

import json
import os
import select
import sys
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from macman import appactivity, appsettings, appsetup, config
from macman.channels.appconfirm import AppConfirmer
from macman.security import permissions

#: How often status is pushed without being asked. Short enough that the menu
#: bar is not stale, long enough to stay invisible in Activity Monitor.
STATUS_INTERVAL_SECONDS = 5.0


@dataclass
class Status:
    """What the menu bar needs to draw itself.

    `sentOut` is here because it is the headline claim, and a number that is
    almost always zero is far more convincing in the menu bar than a sentence
    about privacy in a settings pane.
    """

    running: bool = True
    engine: str = "unknown"
    #: False means the on-device model cannot call tools — see RELIABILITY.md.
    tools: bool = False
    fullDiskAccess: bool = False
    tasksToday: int = 0
    sentOut: int = 0
    detail: str = ""
    error: str | None = None


def _today_counts() -> tuple[int, int]:
    """(tasks, cloud sends) since local midnight, read from the audit log.

    Read rather than kept in memory so the count survives a restart and cannot
    disagree with the log the user can inspect. A malformed line is skipped:
    the status line must never be the thing that crashes the daemon.
    """
    log = config.AUDIT_LOG
    if not log.exists():
        return 0, 0

    midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    tasks = sent = 0
    try:
        with log.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if float(record.get("ts", 0)) < midnight:
                    continue
                event = record.get("event", "")
                if event == "task_start":
                    tasks += 1
                elif event in {"egress_approved", "egress_pre_approved"}:
                    sent += 1
    except OSError:
        return 0, 0
    return tasks, sent


def read_status() -> Status:
    """Assemble the current state. Never raises — a status line that throws
    would take the daemon down with it."""
    status = Status()
    try:
        from macman.engines import local as local_engine
        from macman.preflight import _probe_full_disk_access

        backend = local_engine.apple_backend()
        status.engine = "on-device" if backend.available else "unavailable"
        status.tools = backend.tools
        status.detail = backend.detail
        status.fullDiskAccess = _probe_full_disk_access()
        status.tasksToday, status.sentOut = _today_counts()

        # A model that cannot call tools answers from memory instead of
        # looking things up. That is a broken install, not a degraded one, so
        # it is surfaced rather than hidden behind a green dot.
        if backend.available and not backend.tools:
            status.error = ("The on-device model cannot use tools. Rebuild the "
                            "helpers with -DMACMAN_TOOLS.")
    except Exception as exc:                     # noqa: BLE001 — see docstring
        status.running = False
        status.error = f"{type(exc).__name__}: {exc}"[:200]
    return status


#: Serialises writes: the confirmer runs on a worker thread while the read loop
#: emits status from the main one, and interleaved lines would be unparseable.
_write_lock = threading.Lock()


def _emit(payload: dict[str, Any]) -> None:
    line = json.dumps(payload, sort_keys=True) + "\n"
    with _write_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


def _send_status() -> None:
    _emit({"type": "status", **asdict(read_status())})


def _handle_settings(kind: str, message: dict[str, Any]) -> None:
    """Apply one settings change and report the outcome.

    Every failure comes back as a message the user can act on rather than an
    exception that kills the daemon — a mistyped phone number must not take
    MACman down.

    The reply deliberately re-sends the whole settings payload on success, so
    the window redraws from what is actually on disk rather than from what it
    hoped it wrote.
    """
    try:
        if kind == "settings_set":
            field = str(message.get("field", ""))
            stored = appsettings.set_field(field, message.get("value"))
            detail = f"{field} updated"
        elif kind == "set_cloud_key":
            appsettings.set_cloud_key(str(message.get("key", "")))
            detail = "Claude key saved to your Keychain"
        elif kind == "clear_cloud_key":
            appsettings.clear_cloud_key()
            detail = "Claude key removed"
        elif kind == "add_pre_approval":
            detail = appsettings.add_pre_approval(
                str(message.get("category", "")),
                str(message.get("path", "")),
                message.get("days", 30))
        elif kind == "remove_pre_approval":
            appsettings.remove_pre_approval(int(message.get("index", -1)))
            detail = "Pre-approval removed"
        elif kind == "open_permission":
            permissions.open_settings(str(message.get("key", "")))
            detail = "Opened System Settings"
        else:                                   # unreachable; kept explicit
            raise appsettings.SettingRejected(f"Unknown request {kind!r}.")
    except appsettings.SettingRejected as rejection:
        _emit({"type": "settings_result", "ok": False, "detail": str(rejection)})
        return
    except Exception as exc:                    # noqa: BLE001
        _emit({"type": "settings_result", "ok": False,
               "detail": f"{type(exc).__name__}: {exc}"[:200]})
        return

    _emit({"type": "settings_result", "ok": True, "detail": detail})
    _emit({"type": "settings", **appsettings.read()})
    _send_status()


def _handle_provision(force: bool) -> None:
    """Create a login code and hand back its URI for display as a QR.

    The only outbound secret in the protocol, and only from an action the user
    asked for. It is not logged here, and `appsettings.read()` will never
    return it — see appsetup's module docstring.
    """
    try:
        uri = appsetup.provision(force=force)
    except RuntimeError as exc:
        # Already provisioned and force not set. Not an error worth a stack
        # trace: it is the guard against silently invalidating a working code.
        _emit({"type": "provision_result", "ok": False, "detail": str(exc),
               "already_configured": True})
        return
    except Exception as exc:                     # noqa: BLE001
        _emit({"type": "provision_result", "ok": False,
               "detail": f"{type(exc).__name__}: {exc}"[:200]})
        return
    _emit({"type": "provision_result", "ok": True, "uri": uri})


def _run_self_test() -> None:
    result = appsetup.self_test()
    _emit({"type": "self_test_result", **result.as_dict()})
    _send_status()


def _consent_selftest(confirmer: AppConfirmer) -> None:
    """Ask for consent on a fabricated disclosure and report the answer.

    Runs on a worker thread on purpose: it must block exactly as a real tool
    call does, while the read loop stays free to deliver the reply. If this
    deadlocks, so would every real request, and better to find that here.
    """
    from pathlib import Path

    from macman.security import egress

    disclosure = egress.Disclosure(
        destination=egress.Destination.CLAUDE_CLI,
        precision=egress.Precision.SCOPE,
        reason="self-test — nothing is sent whatever you choose",
        payload=(egress.PayloadItem(str(Path.home() / "projects/example"),
                                    "the whole project folder"),),
        warning=("This is a test of the consent dialog. No data leaves this "
                 "Mac regardless of which button you press."),
        billing="Your Claude subscription — no metered API cost",
        category="coding",
    )
    approved = confirmer.ask(f"sends data to {disclosure.destination.label}",
                             disclosure.as_text())
    _emit({"type": "consent_selftest_result", "approved": approved})


def run() -> int:
    """Serve the app until it closes the pipe or asks us to stop.

    EOF on stdin means the app quit. Exiting then is correct and deliberate:
    the app owns the permissions this process runs under, so a daemon that
    outlived it would be an orphan holding access nobody can see or revoke
    from the menu bar.
    """
    confirmer = AppConfirmer(send=_emit)

    _emit({"type": "ready", "pid": os.getpid()})
    _send_status()

    next_status = time.monotonic() + STATUS_INTERVAL_SECONDS

    while True:
        timeout = max(0.0, next_status - time.monotonic())
        readable, _, _ = select.select([sys.stdin], [], [], timeout)

        if readable:
            line = sys.stdin.readline()
            if not line:                       # EOF — the app is gone
                return 0
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue

            kind = message.get("type")
            if kind == "shutdown":
                return 0
            if kind == "reload":
                # Settings are read fresh at each use, so there is nothing to
                # invalidate — acknowledging keeps the app's state honest.
                _emit({"type": "reloaded"})
                _send_status()
            elif kind == "ping":
                _send_status()
            elif kind == "consent_result":
                # `message.get("ok")` is passed through unconverted on purpose;
                # `resolve` accepts only a real boolean true.
                confirmer.resolve(str(message.get("id", "")), message.get("ok"))
            elif kind == "settings":
                _emit({"type": "settings", **appsettings.read()})
            elif kind == "activity":
                _emit({"type": "activity",
                       **appactivity.read(int(message.get("limit", 100)))})
            elif kind == "setup_status":
                _emit({"type": "setup_status", **appsetup.status()})
            elif kind == "provision_code":
                _handle_provision(bool(message.get("force", False)))
            elif kind == "verify_code":
                _emit({"type": "verify_result",
                       "ok": appsetup.verify(str(message.get("code", "")))})
            elif kind == "self_test":
                # On a worker thread: a local inference takes seconds, and the
                # read loop must stay responsive or the app looks hung.
                threading.Thread(target=_run_self_test, daemon=True).start()
            elif kind in {"settings_set", "set_cloud_key", "clear_cloud_key",
                          "add_pre_approval", "remove_pre_approval",
                          "open_permission"}:
                _handle_settings(kind, message)
            elif kind == "consent_selftest":
                # Exercises the whole consent path — daemon to dialog and back
                # — without needing a cloud key or a real task. The alternative
                # is discovering the dialog is broken the first time something
                # real depends on it.
                threading.Thread(target=_consent_selftest,
                                 args=(confirmer,), daemon=True).start()

        if time.monotonic() >= next_status:
            _send_status()
            next_status = time.monotonic() + STATUS_INTERVAL_SECONDS

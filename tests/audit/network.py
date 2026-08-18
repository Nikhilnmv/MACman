#!/usr/bin/env python3
"""Does anything leave the Mac during a private task?

MACman's central claim is that personal files never leave your machine. That
deserves proof rather than assertion, so this instruments every outbound socket
connection Python can make and runs real private tasks through the local
engine.

    .venv/bin/python tests/audit/network.py

**A pass means zero outbound connections during private tasks.** Anything else
is a broken promise and should be treated as a release blocker.

## What this covers, and what it does not

Covered: every connection attempted by MACman's Python process, which is where
routing, tool execution and the cloud engine live. If the router leaked, or a
tool phoned home, it appears here.

Not covered: the Swift helpers run as separate processes, so their sockets are
invisible to this test — a Python-level patch cannot see another process.

That gap has since been closed **separately**, by observing the helpers with
`lsof -i` while they did real work: `macman-local` held no open socket during a
complete inference, and `macman-speech` held none through recogniser start-up
and audio capture. Results and the remaining caveat are in
RELIABILITY.md ("The Swift helpers — the gap that check couldn't see").

It is recorded there rather than automated here because it needs the helpers
running under observation, which this in-process test cannot arrange.
"""

from __future__ import annotations

import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

#: Connections to these are not "leaving the Mac" — they are local IPC.
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


@dataclass
class Recorder:
    """Records every socket connection attempted while installed."""

    connections: list[str] = field(default_factory=list)
    _original_connect = None
    _original_connect_ex = None

    def install(self) -> None:
        self._original_connect = socket.socket.connect
        self._original_connect_ex = socket.socket.connect_ex
        recorder = self

        def record(address) -> None:
            host = address[0] if isinstance(address, tuple) else str(address)
            if str(host) not in _LOOPBACK:
                recorder.connections.append(str(host))

        def patched_connect(self, address):  # noqa: ANN001
            record(address)
            return recorder._original_connect(self, address)

        def patched_connect_ex(self, address):  # noqa: ANN001
            record(address)
            return recorder._original_connect_ex(self, address)

        socket.socket.connect = patched_connect
        socket.socket.connect_ex = patched_connect_ex

    def remove(self) -> None:
        if self._original_connect:
            socket.socket.connect = self._original_connect
        if self._original_connect_ex:
            socket.socket.connect_ex = self._original_connect_ex


#: Deliberately the most sensitive things a person would ask — documents,
#: mail, notes, files. If anything leaks, it should leak here.
PRIVATE_TASKS = [
    "how many PDF files are in my Downloads folder?",
    "what's in my Documents folder?",
    "how many unread emails do I have?",
    "how many notes do I have?",
    "what reminders do I have outstanding?",
]

#: Routing decisions must also be made without a network call — asking a cloud
#: model "is this private?" has already leaked the filename.
ROUTING_CHECKS = [
    "summarise the contract in ~/Documents/lease.pages",
    "draft an email to my accountant about the invoice",
    "read my medical records",
]


def main() -> int:
    from macman.engines.local import LocalEngine
    from macman.router import route

    recorder = Recorder()
    print("Network audit — does anything leave the Mac during a private task?\n")

    # 1. Routing, which happens before any engine runs.
    recorder.install()
    try:
        for task in ROUTING_CHECKS:
            decision = route(task)
            print(f"  route  {decision.engine.value:<6} {task[:52]}")
    finally:
        recorder.remove()

    routing_leaks = list(recorder.connections)
    print(f"\n  Routing made {len(routing_leaks)} outbound connection(s)"
          f"{': ' + ', '.join(routing_leaks) if routing_leaks else ''}\n")

    # 2. Real private tasks, end to end through the on-device engine.
    recorder.connections.clear()
    recorder.install()
    try:
        for task in PRIVATE_TASKS:
            try:
                answer = LocalEngine().run(task, session_id="audit",
                                           confirm=lambda *_: False)
                print(f"  task   ok     {task[:46]}")
                print(f"                → {answer.strip()[:60]}")
            except Exception as exc:
                print(f"  task   FAIL   {task[:46]} ({type(exc).__name__})")
    finally:
        recorder.remove()

    task_leaks = list(recorder.connections)

    print(f"\n{'─' * 68}")
    print(f"  Routing         : {len(routing_leaks)} outbound connection(s)")
    print(f"  Private tasks   : {len(task_leaks)} outbound connection(s)")

    if task_leaks:
        print("\n  Hosts contacted:")
        for host in sorted(set(task_leaks)):
            print(f"    {host}")

    total = len(routing_leaks) + len(task_leaks)
    print()
    if total == 0:
        print("  PASS — nothing left the Mac.")
        print("  Scope: MACman's Python process. The Swift helpers are separate")
        print("  processes, checked separately with lsof — see RELIABILITY.md.")
        return 0

    print(f"  FAIL — {total} outbound connection(s) during private work.")
    print("  This breaks the project's central claim and blocks release.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

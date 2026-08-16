"""Append-only audit log.

Every tool call MACman makes is recorded before it runs, with the engine that
requested it. This is the record you consult when you want to know what happened
while you were away — so it is written eagerly, and a failure to write is a
failure to act (DESIGN.md §6.4).

Tool *results* are recorded by hash, not content: the log must be safe to read
without re-exposing whatever the tool touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from macman import config


def _digest(value: Any) -> str:
    """Stable short hash of a tool result, for correlation without disclosure."""
    payload = value if isinstance(value, (bytes, bytearray)) else str(value).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


@dataclass
class AuditLog:
    path: Path = field(default_factory=lambda: config.AUDIT_LOG)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, record: dict[str, Any]) -> None:
        """Write one record durably.

        Opened per-write in append mode and fsync'd: MACman is long-running and
        may be killed at any moment, and a log that loses its last entries is
        worthless precisely when it matters.
        """
        record["ts"] = time.time()
        line = json.dumps(record, default=str, sort_keys=True) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def tool_call(
        self,
        *,
        session_id: str,
        engine: str,
        tool: str,
        args: dict[str, Any],
        verdict: str,
        tier: str,
    ) -> None:
        """Record an intended tool call, before execution."""
        self._append({
            "kind": "tool_call",
            "session": session_id,
            "engine": engine,
            "tool": tool,
            "args": args,
            "verdict": verdict,
            "tier": tier,
        })

    def tool_result(
        self, *, session_id: str, tool: str, ok: bool, result: Any, elapsed_ms: int
    ) -> None:
        self._append({
            "kind": "tool_result",
            "session": session_id,
            "tool": tool,
            "ok": ok,
            "result_sha": _digest(result),
            "result_bytes": len(str(result)),
            "elapsed_ms": elapsed_ms,
        })

    def session(self, *, session_id: str, event: str, **extra: Any) -> None:
        """Record a session lifecycle event: auth, engine choice, tier, end."""
        self._append({"kind": "session", "session": session_id, "event": event, **extra})

    def security(self, *, event: str, **extra: Any) -> None:
        """Record a security-relevant event: rejected sender, auth failure, denial."""
        self._append({"kind": "security", "event": event, **extra})

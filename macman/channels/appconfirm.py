"""Confirmation through MACman.app's native dialog.

The third way of asking, alongside `TextConfirmer` (over iMessage) and the
voice loop. Which one is used depends on where the owner is, not on what is
being asked — the same `Disclosure` is rendered by all three.

## Why the dialog is native and not a web page

An earlier design served the UI from `localhost`. A browser extension with host
permissions can read any page you open, **including one served from your own
machine**, and can click its buttons. That is tolerable for showing status and
unacceptable for approving data leaving the Mac. So the settings surface may
display consent history; only the app may grant it.

## The crossing problem

The tool thread calls `ask()` and must block until the owner answers, while the
answer arrives on the bridge's read loop — a different thread. This is the same
shape `TextConfirmer` solves for iMessage: park the asking thread on an
`Event`, and let the reader release it.

Two defaults, both chosen to fail closed:

* **A timeout denies.** An unattended Mac must never approve by outlasting
  someone's attention.
* **Anything unparseable denies**, including a reply for a question that is no
  longer outstanding.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

#: Long enough to read a disclosure and think; short enough that a forgotten
#: dialog does not park a task forever.
DEFAULT_TIMEOUT_SECONDS = 180.0


@dataclass
class AppConfirmer:
    """Asks the owner through MACman.app and blocks until they answer."""

    #: Writes one JSON-serialisable message to the app. Injected so this can be
    #: tested without a running app, and so the bridge owns the actual pipe.
    send: Callable[[dict[str, Any]], None]
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _answered: threading.Event = field(default_factory=threading.Event)
    _pending_id: str | None = None
    _answer: bool = False

    @property
    def pending(self) -> bool:
        with self._lock:
            return self._pending_id is not None

    def ask(self, reason: str, summary: str) -> bool:
        """Show a dialog and wait. Matches the `confirm` callback signature.

        Args:
            reason: Short phrase describing the action, e.g. "sends data to
                Anthropic's API".
            summary: The full disclosure, already rendered.
        """
        request_id = uuid.uuid4().hex[:12]

        with self._lock:
            if self._pending_id is not None:
                # A second dialog while one is open would make the answer
                # ambiguous, and stacking modal alerts is hostile besides.
                return False
            self._pending_id = request_id
            self._answer = False
            self._answered.clear()

        try:
            self.send({
                "type": "consent",
                "id": request_id,
                "reason": reason,
                "body": summary,
            })
        except Exception:                        # noqa: BLE001
            # No app, or a closed pipe: nobody can be asked, so nobody agreed.
            with self._lock:
                self._pending_id = None
            return False

        if not self._answered.wait(self.timeout):
            with self._lock:
                self._pending_id = None
            return False

        with self._lock:
            answer = self._answer
            self._pending_id = None
        return answer

    def resolve(self, request_id: str, approved: Any) -> bool:
        """Deliver the owner's answer. Called from the bridge's read loop.

        Returns whether the reply matched an outstanding question. A reply for
        a question that has already timed out is discarded rather than applied
        to whatever is asked next.

        **Only a real JSON `true` approves.** `bool()` would be catastrophic
        here: `bool("false")` is `True`, so a sender that stringified its
        boolean would turn every refusal into an approval. That bug was written
        and caught during development, which is reason enough to make the
        parsing strict rather than trusting the other side of the pipe.
        """
        with self._lock:
            if self._pending_id is None or request_id != self._pending_id:
                return False
            self._answer = approved is True
        self._answered.set()
        return True

"""Confirmation over a message channel.

The guard's CONFIRM verdict needs an answer from the owner, but the owner is not
at the Mac — the answer arrives as another inbound message. That creates a
crossing problem: the tool call blocks waiting, while the thing that receives
the answer is the poller on another thread.

`TextConfirmer` bridges the two. A pending question parks the tool thread on an
`Event`; the poller checks for a pending question *before* treating a message as
a task, and releases it.

Two defaults matter, both chosen to fail closed:

* **A timeout denies.** An unattended MACman must never approve a destructive
  action by outlasting its owner's attention.
* **An unparseable answer denies.** Only an explicit yes counts.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

#: Answers accepted as approval. Everything else — including silence — denies.
_AFFIRMATIVE = frozenset({"y", "yes", "ok", "okay", "go", "do it", "confirm",
                          "approve", "sure", "yep", "yeah"})

_NEGATIVE = frozenset({"n", "no", "nope", "stop", "cancel", "deny", "don't", "dont"})

DEFAULT_TIMEOUT_SECONDS = 120


@dataclass
class TextConfirmer:
    """Asks the owner to approve a guarded action, over the message channel."""

    #: Sends a message to the owner. Injected so this works over iMessage now
    #: and over a voice channel in v2 without changing the flow.
    send: Callable[[str], bool]
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    _answered: threading.Event = field(default_factory=threading.Event)
    _pending: bool = False
    _answer: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def pending(self) -> bool:
        with self._lock:
            return self._pending

    def ask(self, reason: str, summary: str) -> bool:
        """Ask the owner and block until they answer or the timeout expires.

        Matches the signature the tool registry expects for its `confirm`
        callback, so it drops straight in.
        """
        with self._lock:
            if self._pending:
                # A second question while one is outstanding would make the
                # owner's next reply ambiguous. Deny rather than guess.
                return False
            self._pending = True
            self._answer = False
            self._answered.clear()

        self.send(
            f"MACman wants to do something that {reason}:\n\n{summary}\n\n"
            f"Reply YES to allow, anything else to refuse. "
            f"Refusing automatically in {int(self.timeout)}s."
        )

        answered = self._answered.wait(timeout=self.timeout)

        with self._lock:
            self._pending = False
            approved = self._answer if answered else False

        if not answered:
            self.send("No answer — refused.")
        return approved

    def receive(self, text: str) -> bool:
        """Offer an inbound message as the answer to a pending question.

        Returns:
            True if the message was consumed as an answer, meaning the caller
            must not also treat it as a task.
        """
        with self._lock:
            if not self._pending:
                return False
            cleaned = text.strip().lower().rstrip(".!")
            self._answer = cleaned in _AFFIRMATIVE
            self._answered.set()

        if not self._answer and cleaned not in _NEGATIVE:
            # Distinguish a deliberate no from an unrecognised reply, so the
            # owner isn't left wondering whether their answer registered.
            self.send("Didn't recognise that as a yes — refused.")
        return True

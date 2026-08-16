"""Session model — the v1 orchestration core.

Owns what happens between "a message arrived" and "an engine ran": allowlist,
authentication, conversation history, engine routing, and the kill switch.

The ordering here is the security design (DESIGN.md §6), and it is deliberate:

    allowlist → kill switch → auth → route → engine

An unknown sender is dropped before authentication is even attempted, so an
attacker cannot use MACman as an oracle for whether a code is valid. `STOP` is
honoured before auth, so the kill switch works even from an expired session.

The engine dispatcher is injected rather than imported, which keeps this whole
flow testable without an API key or a real Mac.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

from macman import config
from macman.config import Engine
from macman.router import Route, route
from macman.security import auth, lockstate
from macman.security.audit import AuditLog

#: Ends a session from any state, including an unauthenticated one.
#: Phrased generously on purpose: this is the control someone reaches for when
#: something is going wrong, and it should not fail because they wrote the
#: sentence they were thinking rather than the keyword we chose.
KILL_WORDS = frozenset({
    "stop", "cancel", "abort", "quit",
    "end session", "end the session", "end", "log out", "logout",
    "sign out", "close session", "disconnect",
})
# Note "lock" is deliberately absent: locking the *screen* is a legitimate task
# ("lock my mac"), and it is distinct from ending MACman's session. Conflating
# them would make one of the two silently not happen.


def _normalize(text: str) -> str:
    """Lowercase and strip trailing punctuation, so 'MACman wake up!' matches
    the phrase 'macman wake up' without the caller needing to be exact."""
    return text.strip().lower().rstrip("!.?")


def _matches_wake_phrase(text: str) -> bool:
    normalized = _normalize(text)
    return any(phrase in normalized for phrase in config.WAKE_PHRASES)


def _looks_like_code(text: str) -> bool:
    """Whether a message is nothing but a TOTP code.

    Guards a real leak: once a session is active, any message is treated as a
    task, so a code sent again — a mistake people genuinely make — would be
    handed to an engine as task text, and on a cloud-routed session that means
    transmitting a credential to the API. Requiring the *whole* message to be
    six digits keeps legitimate tasks like "delete file 123456" unaffected.
    """
    digits = "".join(character for character in text if character.isdigit())
    remainder = "".join(character for character in text if not character.isdigit())
    return len(digits) == 6 and remainder.strip() == ""


class State(str, Enum):
    AWAITING_AUTH = "awaiting_auth"
    ACTIVE = "active"


@dataclass
class Conversation:
    """One authenticated exchange with one handle."""

    session: auth.Session
    state: State = State.ACTIVE
    history: list[dict] = field(default_factory=list)
    turns: int = 0
    cost_usd: float = 0.0
    #: Engine used most recently, so a switch can be announced when it changes.
    last_engine: Engine | None = None

    @property
    def handle(self) -> str:
        return self.session.handle

    @property
    def session_id(self) -> str:
        return self.session.session_id

    @property
    def expired(self) -> bool:
        return self.session.expired

    def record(self, task: str, reply: str) -> None:
        self.history.append({"role": "user", "content": task})
        self.history.append({"role": "assistant", "content": reply})
        self.turns += 1
        self.session.touch()


class Dispatcher(Protocol):
    """Runs a task on the chosen engine and returns the reply text."""

    def __call__(self, task: str, *, conversation: Conversation,
                 decision: Route) -> tuple[str, float]:
        ...


@dataclass
class SessionManager:
    """Routes incoming messages through the security pipeline to an engine."""

    dispatch: Dispatcher
    audit: AuditLog = field(default_factory=AuditLog)
    authenticator: auth.Authenticator = field(default_factory=auth.Authenticator)
    allowlist: frozenset[str] = field(default_factory=lambda: config.ALLOWED_HANDLES)
    _conversations: dict[str, Conversation] = field(default_factory=dict)
    #: Handle → when they said the wake phrase. Cleared on success, on
    #: expiry, or once a session starts — never grows unbounded.
    _awake: dict[str, float] = field(default_factory=dict)

    # ----------------------------------------------------------------- #
    # Entry point
    # ----------------------------------------------------------------- #

    def handle_message(self, handle: str, text: str) -> str | None:
        """Process one inbound message.

        Returns:
            The reply to send, or None if the message should be silently
            dropped. Silence is the correct response to an unknown sender —
            any reply confirms the Mac is listening.
        """
        text = text.strip()

        if handle not in self.allowlist:
            self.audit.security(event="sender_rejected", handle=handle,
                                preview=text[:60])
            return None

        if text.lower() in KILL_WORDS:
            return self._end_session(handle)

        conversation = self._conversations.get(handle)

        if conversation is not None and conversation.expired:
            self.audit.session(session_id=conversation.session_id,
                               event="expired", handle=handle)
            del self._conversations[handle]
            conversation = None

        if conversation is None:
            return self._handle_pre_auth(handle, text)

        # Never let a bare code through to an engine, even mid-session.
        if _looks_like_code(text):
            self.audit.security(event="code_suppressed", handle=handle,
                                session=conversation.session_id)
            return "You're already authenticated — no code needed. What do you need?"

        return self._run(conversation, text)

    # ----------------------------------------------------------------- #
    # Wake phrase + authentication
    # ----------------------------------------------------------------- #

    def _is_awake(self, handle: str) -> bool:
        woken_at = self._awake.get(handle)
        if woken_at is None:
            return False
        if time.time() - woken_at > config.WAKE_TIMEOUT_SECONDS:
            del self._awake[handle]
            return False
        return True

    def _handle_pre_auth(self, handle: str, text: str) -> str | None:
        """Route a message from an allowlisted handle with no open session.

        Before the wake phrase exists, this went straight to `_authenticate`,
        which meant any stray text was scored as a guessed code and counted
        toward the lockout. Now nothing is scored, and nothing is even
        answered, until the wake phrase has been said — closer to how a
        person who hasn't been addressed doesn't answer either.
        """
        if self._is_awake(handle):
            return self._authenticate(handle, text)

        if _matches_wake_phrase(text):
            self._awake[handle] = time.time()
            self.audit.session(session_id="-", event="woken", handle=handle)
            return "MACman here. Send your code."

        # Not awake, not a wake phrase: stay silent. Answering anything here
        # — even a rejection — would confirm to a probing sender that this
        # number is listening at all.
        return None

    def _authenticate(self, handle: str, text: str) -> str:
        result = self.authenticator.verify(text)

        if result is auth.AuthResult.OK:
            self._awake.pop(handle, None)
            conversation = Conversation(session=auth.Session.start(handle))
            self._conversations[handle] = conversation
            state = lockstate.read()
            self.audit.session(session_id=conversation.session_id, event="authenticated",
                               handle=handle, tier=state.tier.value)
            return f"MACman ready. {state.explain()} What do you need?"

        self.audit.security(event="auth_failed", handle=handle, result=result.value)

        if result is auth.AuthResult.LOCKED_OUT:
            minutes = max(1, self.authenticator.seconds_until_unlock() // 60)
            return f"Too many failed attempts. Locked for {minutes} more minute(s)."
        if result is auth.AuthResult.NOT_CONFIGURED:
            return ("MACman has no credential set up yet. Run "
                    "`macman auth provision` on the Mac.")
        if result is auth.AuthResult.REPLAYED:
            return "That code was already used. Send the current one."
        return "Send your current MACman code to start a session."

    def _end_session(self, handle: str) -> str:
        conversation = self._conversations.pop(handle, None)
        if conversation is None:
            return "No active session."
        self.audit.session(session_id=conversation.session_id, event="stopped",
                           handle=handle, turns=conversation.turns,
                           cost_usd=round(conversation.cost_usd, 6))
        return (f"Stopped. {conversation.turns} task(s) this session, "
                f"${conversation.cost_usd:.3f}.")

    # ----------------------------------------------------------------- #
    # Execution
    # ----------------------------------------------------------------- #

    def _run(self, conversation: Conversation, task: str) -> str:
        state = lockstate.read()
        if state.tier is lockstate.Tier.UNAVAILABLE:
            return state.explain()

        decision = route(task)

        # A follow-up that carries no signal of its own inherits the running
        # engine, rather than snapping back to the default. Without this, "fix
        # the failing test" after a VS Code task lands on the local engine and
        # the conversation ping-pongs.
        #
        # The asymmetry is deliberate and preserves stickiness: any *positive*
        # private signal still routes local, because inheritance only applies
        # when no rule matched at all.
        if decision.rule == "default" and conversation.last_engine is not None:
            decision = Route(conversation.last_engine, "inherited", None)

        # Announce the engine on the first task and on every switch, so it is
        # never ambiguous whether something just left the Mac.
        preamble = ""
        if decision.engine is not conversation.last_engine:
            preamble = decision.announce() + "\n\n"
            conversation.last_engine = decision.engine

        self.audit.session(
            session_id=conversation.session_id, event="task_start",
            engine=decision.engine.value, rule=decision.rule,
            evidence=decision.evidence, tier=state.tier.value, task=task[:400],
        )

        try:
            reply, cost = self.dispatch(task, conversation=conversation,
                                        decision=decision)
        except Exception as exc:
            self.audit.session(session_id=conversation.session_id,
                               event="task_failed", error=str(exc)[:300])
            return f"{preamble}That failed: {exc}"

        conversation.cost_usd += cost
        conversation.record(task, reply)
        return f"{preamble}{reply}"

    # ----------------------------------------------------------------- #
    # Introspection
    # ----------------------------------------------------------------- #

    def turns_for(self, handle: str) -> int:
        """Tasks run in this handle's session so far.

        Lets a caller tell "a task ran" from "MACman answered a wake phrase or
        an auth prompt", without threading that distinction through the return
        type of `handle_message`.
        """
        conversation = self._conversations.get(handle)
        return conversation.turns if conversation else 0

    def active_sessions(self) -> list[Conversation]:
        return [c for c in self._conversations.values() if not c.expired]

    def sweep_expired(self) -> int:
        """Drop expired sessions. Called periodically by the poller."""
        stale = [h for h, c in self._conversations.items() if c.expired]
        for handle in stale:
            conversation = self._conversations.pop(handle)
            self.audit.session(session_id=conversation.session_id,
                               event="expired", handle=handle)
        return len(stale)

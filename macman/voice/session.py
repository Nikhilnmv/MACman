"""The local voice loop — talk to your Mac, it talks back.

    macman voice

Listen → route → run → speak, using the same 18 primitives and the same guard,
tier checks and audit log as the text channel. The only difference is how a
request arrives and how the answer is delivered.

## Why there is no code to enter here

The iMessage channel authenticates with a TOTP code because a text could come
from anywhere. Speaking into this Mac's microphone means standing in front of
it — physical presence *is* the authentication, and the screen lock already
governs whether anyone can reach the machine at all. Demanding a code from
someone already sitting at the keyboard would be ceremony, not security.

That reasoning does **not** carry over to v3's FaceTime channel, where the
voice arrives over a network from an unverified caller. That path keeps the
wake phrase and the code.

## Confirmations are spoken

A guarded action asks out loud and waits for a spoken yes. Anything that isn't
clearly affirmative — including silence — is a refusal, matching the text
channel's fail-closed behaviour.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from macman.config import Engine
from macman.router import route
from macman.security import lockstate
from macman.security.audit import AuditLog
from macman.voice import speech

#: Spoken replies that count as approval. Everything else refuses.
_AFFIRMATIVE = {"yes", "yeah", "yep", "sure", "okay", "ok", "go ahead",
                "do it", "confirm", "approved", "please do"}

#: Said to end the session.
_STOP_WORDS = {"stop", "exit", "quit", "goodbye", "good bye", "end session",
               "that's all", "thats all", "nothing else"}

#: Long replies are painful to listen to; the local prompt already asks for
#: brevity, and this is the backstop.
MAX_SPOKEN_CHARS = 600


def _normalise(text: str) -> str:
    return text.strip().lower().rstrip(".!?")


@dataclass
class VoiceSession:
    """One run of the local voice loop."""

    audit: AuditLog = field(default_factory=AuditLog)
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    #: Output device for replies. None speaks through the normal output;
    #: "BlackHole 2ch" is what v3 will use to talk into a call.
    output_device: str | None = None

    def speak(self, text: str) -> None:
        trimmed = text.strip()
        if len(trimmed) > MAX_SPOKEN_CHARS:
            trimmed = trimmed[:MAX_SPOKEN_CHARS] + "… that's the short version."
        print(f"  mac › {trimmed}")
        speech.say(trimmed, device=self.output_device)

    def confirm(self, reason: str, summary: str) -> bool:
        """Ask out loud and wait for a spoken yes. Fails closed."""
        self.speak(f"That {reason}. Should I go ahead?")
        reply = speech.listen(max_seconds=12, silence_seconds=1.0)
        if reply is None:
            self.speak("I didn't hear an answer, so I've left it alone.")
            return False

        print(f"  you › {reply}")
        approved = _normalise(reply) in _AFFIRMATIVE
        if not approved:
            self.speak("Left it alone.")
        self.audit.security(event="voice_confirmation", session=self.session_id,
                            reason=reason, approved=approved)
        return approved

    def handle(self, request: str) -> None:
        state = lockstate.read()
        if state.tier is lockstate.Tier.UNAVAILABLE:
            self.speak(state.explain())
            return

        decision = route(request)
        self.audit.session(session_id=self.session_id, event="task_start",
                           engine=decision.engine.value, rule=decision.rule,
                           tier=state.tier.value, task=request[:400], channel="voice")

        if decision.engine is Engine.CLOUD:
            # Spoken aloud for the same reason the text channel prefixes it:
            # you should never be unsure whether something left the Mac.
            self.speak("This one needs Claude, so it isn't staying on this Mac.")

        try:
            if decision.engine is Engine.LOCAL:
                from macman.engines.local import LocalEngine

                reply = LocalEngine(audit=self.audit).run(
                    request, session_id=self.session_id, confirm=self.confirm)
            else:
                from macman.engines.cloud import CloudEngine

                reply = CloudEngine(audit=self.audit).run(
                    request, session_id=self.session_id, confirm=self.confirm).text
        except Exception as exc:
            self.audit.session(session_id=self.session_id, event="task_failed",
                               error=str(exc)[:300])
            self.speak(f"That didn't work. {str(exc)[:160]}")
            return

        self.speak(reply or "Done.")

    def run(self) -> int:
        available = speech.status()
        if not available.available:
            print(f"  Speech isn't available: {available.detail}")
            return 1

        state = lockstate.read()
        print(f"MACman voice — session {self.session_id}")
        print(f"  {available.detail}")
        print(f"  {state.explain()}")
        print("  Say 'stop' when you're done, or press Ctrl-C.\n")
        self.audit.session(session_id=self.session_id, event="voice_start")

        self.speak("Listening.")
        try:
            while True:
                heard = speech.listen()
                if heard is None:
                    continue          # silence just means keep waiting
                print(f"  you › {heard}")

                if _normalise(heard) in _STOP_WORDS:
                    self.speak("Goodbye.")
                    break
                self.handle(heard)
        except KeyboardInterrupt:
            print()
        finally:
            self.audit.session(session_id=self.session_id, event="voice_stop")
        return 0


def main(output_device: str | None = None) -> int:
    return VoiceSession(output_device=output_device).run()

"""The v1 daemon — poll iMessage, run tasks, reply.

Wires the pieces built so far into the loop that makes MACman useful without a
call:

    imessage.poll → confirmer? → SessionManager → router → engine → imessage.send

Runs in the foreground under `macman serve`. It is not a LaunchAgent yet; that
arrives in v4 along with the app bundle.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from macman import config
from macman.agent.tools import screen
from macman.channels import imessage
from macman.channels.confirm import TextConfirmer
from macman.config import Engine
from macman.engines import local as local_engine
from macman.router import Route
from macman.security import lockstate
from macman.security.audit import AuditLog
from macman.session import Conversation, SessionManager

logger = logging.getLogger(__name__)

#: Attach a screenshot to each reply when the screen is unlocked, so a reply
#: carries visual confirmation the way the original project's did. Suppressed
#: when locked, where a capture would be blank anyway.
ATTACH_SCREENSHOT = True


@dataclass
class Daemon:
    audit: AuditLog
    #: Exercise the whole channel without calling an engine. Everything before
    #: dispatch is real — allowlist, TOTP, sessions, routing, replies — so the
    #: plumbing can be validated end to end while both engines are still
    #: blocked on setup.
    dry_run: bool = False

    def __post_init__(self) -> None:
        #: One confirmer per handle: a pending question belongs to the person
        #: who was asked, and their reply must not answer someone else's.
        self._confirmers: dict[str, TextConfirmer] = {}
        self._manager = SessionManager(dispatch=self._dispatch, audit=self.audit)
        #: Set to end the poll loop. `poll()` sleeps internally, so it checks
        #: this rather than relying on the consumer stopping iteration.
        self._stop = threading.Event()

    # ----------------------------------------------------------------- #
    # Embedding
    #
    # `run()` below is the CLI: it prints and blocks until Ctrl-C. MACman.app
    # cannot use it — the app's daemon speaks JSON on stdout, and a stray
    # `print` would corrupt the protocol mid-conversation. So the checks and
    # the loop are separated from the reporting, and the app supplies its own.
    # ----------------------------------------------------------------- #

    def blocked_because(self) -> str | None:
        """Why serving cannot start, or None if it can.

        Returned rather than printed so the same check can become a terminal
        message, a menu bar line, or an audit record.
        """
        if not config.ALLOWED_HANDLES:
            return ("Nobody is on the allowlist, so every message would be "
                    "ignored. Add a handle in Settings.")
        try:
            imessage.latest_rowid()
        except imessage.ChatDBUnavailable as exc:
            return str(exc)
        return None

    def serve_forever(self) -> None:
        """Poll and dispatch until `stop()`. Never prints, never raises.

        Each message is handled on its own thread so a confirmation waiting on
        a human cannot stall the messages queued behind it.
        """
        self.audit.session(session_id="daemon", event="serve_start",
                           dry_run=self.dry_run)
        try:
            for message in imessage.poll(should_stop=self._stop.is_set):
                logger.info("← %s: %s", message.handle, message.text[:60])
                threading.Thread(
                    target=self._handle, args=(message,), daemon=True
                ).start()
        finally:
            self.audit.session(session_id="daemon", event="serve_stop")

    def stop(self) -> None:
        self._stop.set()

    # ----------------------------------------------------------------- #
    # Engine dispatch
    # ----------------------------------------------------------------- #

    def _dispatch(self, task: str, *, conversation: Conversation,
                  decision: Route) -> tuple[str, float]:
        if self.dry_run:
            # Stops short of the engine, but only of the engine: the routing
            # decision reported here is the real one, so a wrong engine choice
            # still shows up in this test rather than hiding until an engine
            # exists to run it.
            return (
                f"[dry run] Would use the {decision.engine.value} engine "
                f"(rule={decision.rule}, evidence={decision.evidence or '—'}).\n"
                f"No engine was called — this is a channel test."
            ), 0.0

        confirmer = self._confirmers[conversation.handle]

        if decision.engine is Engine.LOCAL:
            try:
                # `confirm` is not optional here. Without it the engine falls
                # back to a terminal `input()` prompt, and in the daemon that
                # blocks forever on stdin nobody is watching — a guarded action
                # would hang the session rather than ask.
                return local_engine.LocalEngine(audit=self.audit).run(
                    task,
                    session_id=conversation.session_id,
                    confirm=confirmer.ask,
                    history=conversation.history,
                ), 0.0
            except (local_engine.LocalEngineUnavailable, NotImplementedError) as exc:
                # Never silently escalate a private task to the cloud. Report
                # honestly and let the owner decide.
                return (f"{exc}\n\nReply 'use claude' if you want this sent to "
                        f"Claude anyway."), 0.0

        from macman.engines.cloud import CloudEngine

        outcome = CloudEngine(audit=self.audit).run(
            task,
            session_id=conversation.session_id,
            confirm=confirmer.ask,
            history=conversation.history,
        )
        return outcome.text, outcome.cost.usd

    # ----------------------------------------------------------------- #
    # Message handling
    # ----------------------------------------------------------------- #

    def _confirmer_for(self, handle: str) -> TextConfirmer:
        if handle not in self._confirmers:
            self._confirmers[handle] = TextConfirmer(
                send=lambda text, h=handle: imessage.send(h, text)
            )
        return self._confirmers[handle]

    def _handle(self, message: imessage.Message) -> None:
        confirmer = self._confirmer_for(message.handle)

        # A pending confirmation claims the next message from that handle,
        # before it can be mistaken for a new task.
        if confirmer.receive(message.text):
            return

        turns_before = self._manager.turns_for(message.handle)
        reply = self._manager.handle_message(message.handle, message.text)
        if reply is None:
            return  # unknown sender; silence is the correct response

        # Attach only when a task actually ran. A screenshot of the desktop
        # bolted onto "Send your code" is noise, and it was costing a ~250 KB
        # upload on every wake phrase and auth prompt.
        ran_task = self._manager.turns_for(message.handle) > turns_before

        attachment = None
        if (ran_task and ATTACH_SCREENSHOT
                and lockstate.read().tier is lockstate.Tier.FULL):
            attachment = self._screenshot_path()

        if not imessage.send(message.handle, reply, attachment=attachment):
            logger.warning("Failed to send reply to %s", message.handle)

    def _screenshot_path(self):
        """Capture and stage a screenshot for sending.

        Staged under `imessage.ATTACHMENT_DIR` rather than the state
        directory — Messages refuses to read from Application Support and
        marks such attachments "Not Delivered" without reporting an error.
        """
        try:
            shot = screen.capture(max_edge=1280)
        except screen.ScreenshotError:
            return None
        return imessage.stage_attachment(shot.png)

    # ----------------------------------------------------------------- #
    # Loop
    # ----------------------------------------------------------------- #

    def run(self) -> int:
        """The CLI entry point: print, then block until Ctrl-C."""
        if (blocked := self.blocked_because()) is not None:
            print(f"  {blocked}")
            return 1

        banner = "MACman serving" + (" — DRY RUN, no engine will be called"
                                     if self.dry_run else "")
        print(f"{banner}. Allowed: {', '.join(sorted(config.ALLOWED_HANDLES))}")
        print(f"  {lockstate.read().explain()}")
        print("  Ctrl-C to stop.\n")

        try:
            self.serve_forever()
        except KeyboardInterrupt:
            print("\n  Stopped.")
        return 0


def main(dry_run: bool = False) -> int:
    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    return Daemon(audit=AuditLog(), dry_run=dry_run).run()

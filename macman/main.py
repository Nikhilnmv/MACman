"""MACman CLI — the v0 supervisor.

Ties the pieces together: read lock state, route the task, dispatch to an
engine, report what it cost.

    macman preflight                  # permission and backend status
    macman route "<task>"             # show the routing decision, run nothing
    macman run "<task>"               # execute one task
    macman repl                       # interactive session
    macman auth provision|status|revoke

The channels (iMessage, FaceTime) arrive in v1 and v2. Until then this is the
front door.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

from macman import config, preflight
from macman.config import Engine
from macman.engines import local as local_engine
from macman.router import route
from macman.security import auth, lockstate


def _load_dotenv() -> None:
    """Load a project-local `.env`.

    Deliberately does *not* overwrite variables already set in the environment:
    on this machine `ANTHROPIC_API_KEY` may belong to another tool, and
    clobbering it in-process would be a surprising side effect.
    """
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_preflight(_: argparse.Namespace) -> int:
    code = preflight.main()
    print("\n  Engines")
    for backend in local_engine.status():
        mark = "OK  " if backend.available else "    "
        print(f"    [{mark}] local/{backend.name:<14} {backend.detail}")
    # Presence is not validity. A key that is set but malformed fails at the
    # first request, which is a slow and confusing way to learn about it — this
    # machine already has an unrelated tool's token in that variable.
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        cloud_ready, cloud_detail = False, "ANTHROPIC_API_KEY not set"
    elif not key.startswith("sk-ant-"):
        cloud_ready, cloud_detail = False, (
            f"ANTHROPIC_API_KEY is set but doesn't look like a console key "
            f"(starts with {key[:8]!r}, expected 'sk-ant-')"
        )
    else:
        cloud_ready, cloud_detail = True, "ANTHROPIC_API_KEY looks valid"
    print(f"    [{'OK  ' if cloud_ready else '    '}] cloud             {cloud_detail}")

    print("\n  Auth")
    print(f"    [{'OK  ' if auth.is_configured() else '    '}] TOTP              "
          f"{'provisioned' if auth.is_configured() else 'not provisioned (macman auth provision)'}")
    return code


def cmd_route(args: argparse.Namespace) -> int:
    decision = route(args.task, frontmost_app=args.frontmost)
    print(f"  engine   {decision.engine.value}")
    print(f"  rule     {decision.rule}")
    print(f"  evidence {decision.evidence or '—'}")
    print(f"\n  {decision.announce()}")
    return 0


def _run_task(task: str, *, session_id: str, frontmost: str | None,
              force_cloud: bool) -> int:
    state = lockstate.read()
    decision = route(task, frontmost_app=frontmost)

    if state.tier is lockstate.Tier.UNAVAILABLE:
        print(f"  {state.explain()}")
        return 1

    engine = decision.engine
    if engine is Engine.LOCAL and force_cloud:
        # Escalation is never automatic; it is this explicit flag, and it is
        # announced so the decision is never silent.
        print("  ⚠️  Overriding a private routing decision — this task WILL be "
              "sent to Claude.")
        print(f"     Routed local because: {decision.rule} = {decision.evidence}")
        engine = Engine.CLOUD

    print(f"  {decision.announce()}")
    if state.tier is lockstate.Tier.HEADLESS:
        print(f"  {state.explain()}")

    if engine is Engine.LOCAL:
        try:
            print(local_engine.LocalEngine().run(task, session_id=session_id))
            return 0
        except (local_engine.LocalEngineUnavailable, NotImplementedError) as exc:
            print(f"\n  {exc}")
            print("\n  To send this to Claude anyway, re-run with --force-cloud.")
            return 2

    # Imported lazily so a missing API key doesn't break `preflight` or `route`.
    from macman.engines.cloud import CloudEngine

    try:
        outcome = CloudEngine().run(task, session_id=session_id)
    except RuntimeError as exc:
        print(f"\n  {exc}")
        return 2

    print(f"\n{outcome.text}\n")
    print(f"  [turns={outcome.turns} stop={outcome.stop_reason} "
          f"cost={outcome.cost.summary()}]")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    return _run_task(
        args.task, session_id=uuid.uuid4().hex[:12],
        frontmost=args.frontmost, force_cloud=args.force_cloud,
    )


def cmd_repl(args: argparse.Namespace) -> int:
    session_id = uuid.uuid4().hex[:12]
    state = lockstate.read()
    print(f"MACman — session {session_id}")
    print(f"  {state.explain()}")
    print("  Ctrl-D or 'exit' to quit.\n")

    while True:
        try:
            task = input("› ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not task:
            continue
        if task in {"exit", "quit"}:
            return 0
        _run_task(task, session_id=session_id, frontmost=args.frontmost,
                  force_cloud=False)
        print()


def cmd_auth(args: argparse.Namespace) -> int:
    if args.action == "status":
        print(f"  TOTP: {'provisioned' if auth.is_configured() else 'not provisioned'}")
        print(f"  Keychain service: {config.KEYCHAIN_SERVICE}")
        return 0

    if args.action == "revoke":
        auth.revoke()
        print("  Revoked. MACman will refuse all sessions until re-provisioned.")
        return 0

    try:
        uri = auth.provision(force=args.force)
    except RuntimeError as exc:
        print(f"  {exc}")
        return 1

    print("  Add this to your authenticator app.\n")
    print(f"  {uri}\n")
    print("  Render it as a QR code with:")
    print(f"    python3 -c \"import qrcode,sys; qrcode.make(sys.argv[1]).show()\" '{uri}'")
    print("\n  Store nothing — the secret is in your Keychain and this URI is "
          "shown once.")
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main() -> int:
    _load_dotenv()

    parser = argparse.ArgumentParser(prog="macman", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="guided first-time setup (start here)")
    sub.add_parser("preflight", help="permission, engine, and auth status")
    p_serve = sub.add_parser("serve", help="poll iMessage and run tasks (v1 daemon)")
    p_serve.add_argument("--dry-run", action="store_true",
                         help="exercise the full channel without calling an engine")

    for name, help_text in (("route", "show the routing decision without running"),
                            ("run", "execute one task")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("task")
        p.add_argument("--frontmost", default=None,
                       help="frontmost app, used only if the task names none")
        if name == "run":
            p.add_argument("--force-cloud", action="store_true",
                           help="override a private routing decision (announced)")

    p_repl = sub.add_parser("repl", help="interactive session")
    p_repl.add_argument("--frontmost", default=None)

    p_auth = sub.add_parser("auth", help="manage the TOTP credential")
    p_auth.add_argument("action", choices=["provision", "status", "revoke"])
    p_auth.add_argument("--force", action="store_true",
                        help="replace an existing secret (invalidates the old one)")

    args = parser.parse_args()

    if args.command == "setup":
        from macman import setup

        return setup.main()

    if args.command == "serve":
        # Imported lazily: the daemon pulls in the channel stack, which the
        # other commands don't need.
        from macman import serve

        return serve.main(dry_run=args.dry_run)

    return {
        "preflight": cmd_preflight, "route": cmd_route, "run": cmd_run,
        "repl": cmd_repl, "auth": cmd_auth,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

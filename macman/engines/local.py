"""Local engine — the private task set, entirely on-device.

Apple's `FoundationModels`, reached through the `macman-local` helper. It ships
with macOS 26, so the private half of MACman costs a user **nothing to
install**: no download, no daemon, no disk, no API key.

Ollama used to sit behind this as an escalation tier and has been removed. Its
only advantage was tool calling, and it cost 5 GB, a permanent background
daemon, significant RAM and battery, ~10x the latency, and worse
instruction-following. Once Apple's model can call tools, there is nothing left
for it to be better at. `git log` has the implementation if it's ever wanted
back.

## Two capability levels

The helper reports whether it was built with tool support:

* **`tools: true`** — built with `-DMACMAN_TOOLS` (needs full Xcode for the
  `FoundationModelsMacros` plugin). The model can run commands and script apps.
* **`tools: false`** — the default build. The model can reason about text but
  cannot take actions, and says so rather than guessing at answers.

## Where the guard stays

Tool calls do **not** execute in Swift. The helper proxies each one back here
over stdio, so `guard.classify`, the credential-path denials, the tier checks
and the audit log all apply exactly as they do for the cloud engine. The
on-device engine must not be the one place where `cat ~/.ssh/id_rsa` quietly
works.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from macman import config
from macman.agent import prompts
from macman.agent.tools.registry import ToolContext, set_context
from macman.agent.tools.actions import local_tool_by_name
from macman.security import lockstate
from macman.security.audit import AuditLog

_FOUNDATION_MODELS = Path("/System/Library/Frameworks/FoundationModels.framework")

#: Built by `swift build` in helpers/. Debug path is checked too so the helper
#: works during development without a release build.
_HELPER_CANDIDATES = (
    config.HELPERS_BIN / "macman-local",
    config.HELPERS_BIN.parent / "debug" / "macman-local",
)

#: Generous: the model is fast, but a tool-using task makes several round trips
#: through Python, and a slow `find` shouldn't kill the session.
HELPER_TIMEOUT_SECONDS = 180

#: Cap on tool output handed back to the model. Apple's context is smaller than
#: Claude's, and an unbounded directory listing crowds out the question — the
#: exact failure that made the previous engine answer from assumption.
MAX_TOOL_OUTPUT_CHARS = 4_000


class LocalEngineUnavailable(RuntimeError):
    """Raised when the on-device model cannot serve a private task.

    Deliberately *not* a fallback to the cloud. A private task escalating to
    Claude without explicit consent is the one failure this design exists to
    prevent — so this raises, and the caller asks the owner.
    """


@dataclass(frozen=True)
class Backend:
    name: str
    available: bool
    detail: str
    #: Whether this backend can take actions, not just reason.
    tools: bool = False


def helper_path() -> Path | None:
    return next((path for path in _HELPER_CANDIDATES if path.exists()), None)


def apple_backend() -> Backend:
    """Availability of Apple's on-device model, as the helper reports it."""
    if not _FOUNDATION_MODELS.exists():
        return Backend("apple", False, "requires macOS 26 with Apple Intelligence")

    helper = helper_path()
    if helper is None:
        return Backend("apple", False,
                       "macman-local helper not built — run `swift build` in helpers/")

    try:
        result = subprocess.run([str(helper), "check"], capture_output=True,
                                text=True, timeout=20)
        report = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return Backend("apple", False, f"helper did not respond ({exc})")

    detail = report.get("detail", "unknown")
    tools = bool(report.get("tools", False))
    if not report.get("available"):
        return Backend("apple", False, detail, tools)

    return Backend(
        "apple", True,
        "ready" if tools else "ready (text only — rebuild with Xcode for actions)",
        tools,
    )


def status() -> list[Backend]:
    return [apple_backend()]


def available() -> bool:
    return any(backend.available for backend in status())


def _default_confirm(reason: str, summary: str) -> bool:
    print(f"\n  ⚠️  MACman wants to run something that {reason}:\n     {summary}")
    return input("     Allow? [y/N] ").strip().lower() in {"y", "yes"}


@dataclass
class LocalEngine:
    """Runs a private task on Apple's on-device model."""

    audit: AuditLog = field(default_factory=AuditLog)

    def _serve_tool_call(self, request: dict[str, Any]) -> str:
        """Execute one proxied tool call through the guarded registry.

        The Swift side never runs anything itself; this is the only place a
        tool actually executes, which is what keeps the guard, the tier check
        and the audit log in force for the on-device engine too.
        """
        name = request.get("name", "")
        arguments = request.get("arguments") or {}

        # Typed tools only — the on-device model does not get raw `bash`.
        tool = local_tool_by_name(name)
        if tool is None:
            return f"Unknown tool {name!r}."

        output = str(tool.call(arguments))
        if len(output) > MAX_TOOL_OUTPUT_CHARS:
            output = (
                f"{output[:MAX_TOOL_OUTPUT_CHARS]}\n\n[truncated — showed "
                f"{MAX_TOOL_OUTPUT_CHARS} characters. Re-run with a narrower "
                f"command (head, grep, wc -l) if you need more.]"
            )
        return output

    def run(
        self,
        task: str,
        *,
        session_id: str,
        confirm: Callable[[str, str], bool] | None = None,
        history: list[dict] | None = None,
    ) -> str:
        backend = apple_backend()
        if not backend.available:
            raise LocalEngineUnavailable(
                f"The on-device model isn't available: {backend.detail}. This task "
                f"was routed as private, so it will not be sent to Claude without "
                f"your explicit say-so."
            )

        helper = helper_path()
        state = lockstate.read()

        set_context(ToolContext(
            session_id=session_id, engine="local", audit=self.audit,
            confirm=confirm or _default_confirm,
        ))
        self.audit.session(session_id=session_id, event="task_start", engine="local",
                           tier=state.tier.value, tools=backend.tools, task=task[:400])

        # `LOCAL_SYSTEM`, not Claude's `STABLE_SYSTEM` — see prompts.py for the
        # measurement. Claude's prompt drops tool use from 4/4 to 1/4 here.
        instructions = prompts.LOCAL_SYSTEM + "\n\n" + prompts.session_system(state)
        if not backend.tools:
            # Being explicit beats letting a model without tools invent an
            # answer — the failure mode that had the previous engine reporting
            # "2 PDF files" for a folder containing 25.
            instructions += prompts.LOCAL_NO_TOOLS

        started = time.monotonic()
        process = subprocess.Popen(
            [str(helper), "generate", "--instructions", instructions],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )

        try:
            process.stdin.write(json.dumps({"prompt": task}) + "\n")
            process.stdin.flush()

            reply = self._pump(process)
        except (OSError, ValueError) as exc:
            process.kill()
            raise LocalEngineUnavailable(f"on-device helper failed: {exc}") from exc
        finally:
            process.terminate()

        self.audit.session(session_id=session_id, event="task_end", engine="local",
                           elapsed_s=round(time.monotonic() - started, 1))
        return reply

    def _pump(self, process: subprocess.Popen) -> str:
        """Read the helper's output, answering tool requests until it finishes.

        The helper emits one JSON object per line: either a `tool_request` we
        must satisfy, or the final result.
        """
        deadline = time.monotonic() + HELPER_TIMEOUT_SECONDS

        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                stderr = (process.stderr.read() or "").strip()
                raise LocalEngineUnavailable(
                    f"on-device helper exited unexpectedly. {stderr[:200]}"
                )

            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # ignore any non-protocol chatter on stdout

            if message.get("type") == "tool_request":
                result = self._serve_tool_call(message)
                process.stdin.write(json.dumps({"type": "tool_result",
                                                "content": result}) + "\n")
                process.stdin.flush()
                continue

            if message.get("ok"):
                return (message.get("content") or "").strip() or "(no response)"
            raise LocalEngineUnavailable(message.get("error") or "unknown helper error")

        raise LocalEngineUnavailable(
            f"the on-device model did not finish within {HELPER_TIMEOUT_SECONDS}s"
        )

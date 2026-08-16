"""Tier 1 — shell.

The widest-coverage tool on a Mac, and the one that survives a locked screen.
Most of what people ask for reduces to a command here rather than to clicking.

Three hard properties, all enforced in code rather than by prompting:

* API credentials are scrubbed from the child environment.
* Output is truncated, because a runaway command must not blow the context window.
* Every call has a timeout, because a hung command must not wedge a session.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

from macman import config

#: Truncation limit for combined stdout/stderr. A screenshot costs ~2000 tokens;
#: an unbounded `find /` costs far more and says less.
MAX_OUTPUT_CHARS = 30_000

DEFAULT_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class ShellResult:
    command: str
    exit_code: int
    output: str
    truncated: bool
    elapsed_ms: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def for_model(self) -> str:
        """Render for the model, leading with the outcome.

        Truncation is stated explicitly so the model knows the output is partial
        and can narrow the command rather than drawing conclusions from a slice.
        """
        header = f"exit {self.exit_code} ({self.elapsed_ms} ms)"
        body = self.output or "(no output)"
        if self.truncated:
            body += f"\n\n[truncated at {MAX_OUTPUT_CHARS} characters]"
        return f"{header}\n{body}"


def _child_env() -> dict[str, str]:
    """Environment for the child, minus anything secret.

    MACman's own API key must not be readable by a command MACman was talked
    into running — this is the code-level half of the guarantee that `guard.py`
    enforces at the pattern level.
    """
    env = dict(os.environ)
    for name in config.SCRUBBED_ENV_VARS:
        env.pop(name, None)
    return env


def run(
    command: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
) -> ShellResult:
    """Run `command` under `/bin/zsh` and capture combined output.

    Args:
        command: Shell command line, as the model wrote it.
        timeout: Seconds before the command is killed.
        cwd: Working directory; defaults to the home directory.

    Note:
        This is not a sandbox. It runs as your user with your permissions.
        Containment comes from `guard.py`, from MACman never having root, and
        from the screen lock it cannot bypass.
    """
    started = time.monotonic()
    try:
        completed = subprocess.run(
            ["/bin/zsh", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_child_env(),
            cwd=cwd or os.path.expanduser("~"),
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        exit_code = completed.returncode
    except subprocess.TimeoutExpired:
        output = f"Command exceeded the {timeout}s timeout and was terminated."
        exit_code = 124
    except OSError as exc:
        output = f"Failed to launch command: {exc}"
        exit_code = 126

    elapsed_ms = int((time.monotonic() - started) * 1000)
    truncated = len(output) > MAX_OUTPUT_CHARS

    return ShellResult(
        command=command,
        exit_code=exit_code,
        output=output[:MAX_OUTPUT_CHARS],
        truncated=truncated,
        elapsed_ms=elapsed_ms,
    )

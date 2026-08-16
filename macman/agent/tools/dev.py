"""Level 3 — developer tools: VS Code and Claude Code.

The idea from the vision: **MACman orchestrates, the app's own agent does the
work.** MACman does not reimplement Claude's reasoning or VS Code's editor — it
opens the right project and hands over the task.

## Why the CLI and not the GUI

Neither Claude.app nor VS Code ships an AppleScript dictionary — verified, no
`.sdef` in either bundle. Driving their windows would mean Accessibility
automation, which measured **50%** (RELIABILITY.md): clicking into a text
field, hoping the right conversation has focus, hoping submit lands. For an
action that sends a prompt and edits files, a coin flip is not acceptable.

Both ship a supported command-line interface instead:

* `code` — open a folder, a file, or a file at a line. Deterministic, free.
* `claude -p` — hand a task to Claude Code non-interactively and get the
  result. Documented, supported, and needs no UI automation at all.

Same outcome as driving the GUI, none of the guessing. This is also the reason
the vision's "open Claude, navigate to the conversation, type the prompt" is
implemented as "give Claude Code the task" — the destination matters, the
clicking does not.

## Cost

`claude_code` is the one tool here that spends money and edits files, so it
always asks first, naming the project and the task.
"""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from anthropic.lib.tools import beta_tool

from macman.agent.tools import shell as shell_tool
from macman.agent.tools.registry import _guarded, require_confirmation
from macman.agent.tools.typed import PathRefused, _safe_folder

#: Claude Code can run for a long time on a real task.
CLAUDE_TIMEOUT_SECONDS = 600

#: Enough to be useful over a text message without flooding it.
MAX_REPLY_CHARS = 2_000


def _vscode_cli() -> str | None:
    """Locate the `code` CLI, falling back to the one inside the app bundle.

    The bundled binary exists even when the user never ran "Install 'code'
    command in PATH", so this works on a fresh machine.
    """
    found = shutil.which("code")
    if found:
        return found
    bundled = Path("/Applications/Visual Studio Code.app/Contents/Resources/"
                   "app/bin/code")
    return str(bundled) if bundled.exists() else None


@beta_tool
def vscode_control(action: str, path: str = "", line: int = 0) -> str:
    """Open a project or file in VS Code.

    Args:
        action: One of "open_project", "open_file", "new_window".
        path: Folder or file to open, e.g. "~/code/nimoriz".
        line: Line number to jump to, for "open_file". Omit for the top.
    """
    def run() -> str:
        cli = _vscode_cli()
        if cli is None:
            return ("VS Code doesn't appear to be installed, or its command "
                    "line tool is missing.")

        key = action.strip().lower()
        if key not in {"open_project", "open_file", "new_window"}:
            return (f"Unknown action {action!r}. Choose one of: open_project, "
                    f"open_file, new_window.")
        if not path.strip():
            return f"{key} needs a path."

        try:
            target = _safe_folder(path)
        except PathRefused as exc:
            return str(exc)
        if not target.exists():
            return f"{target} does not exist."

        if key == "open_file" and line > 0:
            command = f"{shlex.quote(cli)} -g {shlex.quote(f'{target}:{line}')}"
            where = f"{target.name} at line {line}"
        else:
            flag = "-n " if key == "new_window" else ""
            command = f"{shlex.quote(cli)} {flag}{shlex.quote(str(target))}"
            where = target.name

        result = shell_tool.run(command, timeout=30)
        return (f"Opened {where} in VS Code." if result.ok
                else f"Could not open it: {result.output[:150]}")

    return _guarded("bash", {"vscode_control": action, "path": path}, run)


@beta_tool
def claude_code(task: str, project: str = "") -> str:
    """Hand a coding task to Claude Code, which does the work itself.

    Use this for anything requiring real code reasoning — fixing a bug,
    explaining an error, writing or refactoring code, running and repairing
    tests. Claude Code has its own tools and will edit files in the project.

    Args:
        task: What Claude should do, in plain language.
        project: Project folder to work in, e.g. "~/code/nimoriz". Defaults to
            the home directory.
    """
    def run() -> str:
        cli = shutil.which("claude")
        if cli is None:
            return ("Claude Code isn't installed. Install it, then this can "
                    "hand off coding tasks.")
        if not task.strip():
            return "Say what Claude should do."

        try:
            folder = _safe_folder(project) if project.strip() else Path.home()
        except PathRefused as exc:
            return str(exc)
        if not folder.is_dir():
            return f"{folder} is not a folder."

        # Claude Code edits files and costs money, so this is always confirmed
        # with both the destination and the task spelled out.
        if not require_confirmation(
                "hands a task to Claude Code, which will read and may edit "
                "files in that project, and uses your Claude account",
                f"in {folder}: {task.strip()[:160]}"):
            return "Refused by the owner — nothing was sent to Claude."

        result = shell_tool.run(
            f"{shlex.quote(cli)} -p {shlex.quote(task.strip())}",
            timeout=CLAUDE_TIMEOUT_SECONDS, cwd=str(folder),
        )
        output = result.output.strip()

        # Auth failure is the likely first-run outcome and its message is
        # cryptic, so it is translated rather than passed through.
        if "invalid api key" in output.lower() or "api key" in output.lower():
            return ("Claude Code can't authenticate. Its API key is missing or "
                    "invalid — check `claude` works in a terminal first, then "
                    "try again.")
        if not result.ok:
            return f"Claude Code failed: {output[:MAX_REPLY_CHARS]}"
        if not output:
            return "Claude Code finished but returned nothing."
        return (output[:MAX_REPLY_CHARS] + "\n\n[truncated]"
                if len(output) > MAX_REPLY_CHARS else output)

    return _guarded("bash", {"claude_code": task, "project": project}, run)


#: Level 3 primitives.
DEV_TOOLS = [vscode_control, claude_code]

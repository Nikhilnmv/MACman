"""Level 2 primitives — application automation.

Controls the apps you already have, using whatever mechanism each exposes, in
descending order of reliability (CAPABILITY.md §4):

1. **AppleScript dictionary** — deterministic, and richer on macOS than people
   expect. Pages, Numbers, Keynote, Safari, Chrome, Music, Spotify and Mail all
   ship one.
2. **Shortcuts** — `shortcuts run`. Covers modern apps that never shipped
   AppleScript, and is user-extensible: anything you can build in Shortcuts
   becomes something MACman can do, without code.
3. **URL schemes** — `spotify:`, `vscode://`, `whatsapp://`. Jump straight to a
   chat, file or project, skipping UI navigation entirely.
4. Accessibility — measured at 50%, so it is deliberately *not* used here.

Same shape as Level 1: typed action fields, Python builds the script. The model
picks an enum and fills a slot, which is what it does reliably.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from macman.agent.tools.schema import tool

from macman.agent.tools import shell as shell_tool
from macman.agent.tools.applescript import run as run_applescript
from macman.agent.tools.registry import _guarded, require_confirmation
from macman.agent.tools.typed import PathRefused, _safe_folder


def _app_installed(name: str) -> bool:
    """Whether an app exists, by asking LaunchServices for its bundle id.

    Not `mdfind` — Spotlight may not have indexed, and system apps live under
    `/System/Applications`, so a path search misses Safari and Music entirely.
    LaunchServices knows regardless of location or index state, and errors
    cleanly for apps that genuinely aren't installed.
    """
    result = shell_tool.run(
        f"osascript -e {shlex.quote(f'id of app \"{name}\"')}", timeout=15)
    return result.ok and "." in result.output.strip()


def _running(name: str) -> bool:
    result = run_applescript(
        f'tell application "System Events" to return (exists process "{name}")')
    return result.ok and "true" in result.output.lower()


# --------------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------------- #

#: Both expose the same verbs, so one implementation covers them.
_MEDIA_APPS = ("Spotify", "Music")


def _media_app(preferred: str = "") -> str | None:
    """Pick a music app: the requested one, else whichever is already playing."""
    if preferred:
        match = next((a for a in _MEDIA_APPS if a.lower() in preferred.lower()), None)
        if match and _app_installed(match):
            return match
        return None
    for app in _MEDIA_APPS:
        if _running(app):
            return app
    return next((a for a in _MEDIA_APPS if _app_installed(a)), None)


@tool
def media_control(action: str, app: str = "", query: str = "") -> str:
    """Control music playback in Spotify or Apple Music.

    Args:
        action: One of "play", "pause", "next", "previous", "now_playing",
            "search".
        app: "Spotify" or "Music". Leave empty to use whichever is playing.
        query: Search text, only for "search".
    """
    def run() -> str:
        target = _media_app(app)
        if target is None:
            return "Neither Spotify nor Apple Music appears to be installed."

        key = action.strip().lower()

        if key == "search":
            if not query.strip():
                return "Give something to search for."
            if target != "Spotify":
                return ("Search is only wired up for Spotify. Apple Music has no "
                        "scriptable search — open it and search by hand.")
            # Spotify's URL scheme opens results; it cannot start playback of a
            # chosen result, so say so rather than implying it played.
            shell_tool.run(f"open {shlex.quote('spotify:search:' + query.strip())}",
                           timeout=15)
            return (f"Opened a Spotify search for {query.strip()!r}. I can't pick a "
                    f"result to play — say 'play' once you've chosen one.")

        verbs = {"play": "play", "pause": "pause",
                 "next": "next track", "previous": "previous track"}
        if key in verbs:
            result = run_applescript(f'tell application "{target}" to {verbs[key]}')
            return (f"{key.capitalize()} — {target}." if result.ok
                    else f"Could not control {target}: {result.for_model()[:120]}")

        if key == "now_playing":
            # Asking for the current track while stopped raises -1728, which is
            # an ordinary state rather than a failure — check first and say so.
            result = run_applescript(
                f'tell application "{target}"\n'
                f'  if player state is stopped then return "__STOPPED__"\n'
                f'  return (name of current track) & " — " & (artist of current track)\n'
                f'end tell')
            if not result.ok:
                return f"Could not read {target}: {result.for_model()[:120]}"
            text = result.output.strip()
            return (f"Nothing is playing in {target}." if text == "__STOPPED__"
                    else text or f"Nothing is playing in {target}.")

        return (f"Unknown action {action!r}. Choose one of: play, pause, next, "
                f"previous, now_playing, search.")

    return _guarded("applescript",
                    {"media_control": action, "app": app, "query": query}, run)


# --------------------------------------------------------------------------- #
# Browser
# --------------------------------------------------------------------------- #

_BROWSERS = ("Safari", "Google Chrome", "Arc", "Firefox")


def _browser(preferred: str = "") -> str | None:
    if preferred:
        match = next((b for b in _BROWSERS if preferred.lower() in b.lower()), None)
        if match and _app_installed(match):
            return match
        return None
    return (next((b for b in _BROWSERS if _running(b)), None)
            or next((b for b in _BROWSERS if _app_installed(b)), None))


@tool
def browser_control(action: str, target: str = "", browser: str = "") -> str:
    """Open pages, search the web, or read the current page in a browser.

    Args:
        action: One of "open", "search", "current_url", "page_text", "new_tab",
            "list_tabs".
        target: A URL for "open"/"new_tab", or search words for "search".
        browser: "Safari", "Chrome", "Arc" or "Firefox". Empty picks whichever
            is open.
    """
    def run() -> str:
        app = _browser(browser)
        if app is None:
            return "No supported browser found."

        key = action.strip().lower()

        if key in {"open", "new_tab", "search"}:
            if not target.strip():
                return f"Give {'a URL' if key != 'search' else 'something to search for'}."
            if key == "search":
                from urllib.parse import quote_plus
                url = f"https://duckduckgo.com/?q={quote_plus(target.strip())}"
            else:
                url = target.strip()
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
            result = run_applescript(
                f'tell application "{app}" to open location "{url}"')
            if not result.ok:
                return f"Could not open it: {result.for_model()[:120]}"
            run_applescript(f'tell application "{app}" to activate')
            return f"Opened {url} in {app}."

        # Querying `window 1` with no windows open raises -1719 — again an
        # ordinary state, so every window query guards on the count first.
        def guarded(body: str, empty: str) -> str:
            result = run_applescript(
                f'tell application "{app}"\n'
                f'  if (count of windows) is 0 then return "__NOWINDOWS__"\n'
                f'  {body}\n'
                f'end tell', timeout=30)
            if not result.ok:
                return f"Could not ask {app}: {result.for_model()[:120]}"
            text = result.output.strip()
            return empty if text == "__NOWINDOWS__" else (text or empty)

        if key == "current_url":
            return guarded("return URL of current tab of window 1",
                           f"No pages are open in {app}.")

        if key == "page_text":
            # Safari returns document text directly; Chromium browsers need
            # JavaScript, which requires "Allow JavaScript from Apple Events".
            body = ("return text of current tab of window 1" if app == "Safari"
                    else "return execute front window's active tab javascript "
                         "\"document.body.innerText\"")
            text = guarded(body, f"No pages are open in {app}.")
            if text.startswith("Could not ask") and app != "Safari":
                return (f"{text}\n\nIn {app}, enable Develop → Allow JavaScript "
                        f"from Apple Events.")
            return (text[:3000] + "\n\n[truncated]") if len(text) > 3000 else text

        if key == "list_tabs":
            return guarded("return name of every tab of window 1",
                           f"No tabs are open in {app}.")

        return (f"Unknown action {action!r}. Choose one of: open, search, "
                f"current_url, page_text, new_tab, list_tabs.")

    return _guarded("applescript",
                    {"browser_control": action, "target": target}, run)


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #

_DOC_APPS = {"pages": "Pages", "numbers": "Numbers", "keynote": "Keynote"}


@tool
def document_control(action: str, path: str = "", app: str = "") -> str:
    """Open, read or export Pages, Numbers and Keynote documents.

    Args:
        action: One of "open", "read", "export_pdf", "close", "list_open".
        path: The document to act on, e.g. "~/Documents/resume.pages".
        app: "Pages", "Numbers" or "Keynote". Inferred from the file extension
            when left empty.
    """
    def run() -> str:
        key = action.strip().lower()

        target = _DOC_APPS.get(app.strip().lower(), "")
        if not target and path:
            target = _DOC_APPS.get(Path(path).suffix.lstrip(".").lower(), "")
        if not target:
            return "Say which app: Pages, Numbers or Keynote."

        if key == "list_open":
            result = run_applescript(
                f'tell application "{target}" to return name of every document')
            return result.output.strip() or f"No documents open in {target}." \
                   if result.ok else f"Could not ask {target}: {result.for_model()[:120]}"

        if not path.strip():
            return f"{key} needs a document path."
        try:
            document = _safe_folder(path)
        except PathRefused as exc:
            return str(exc)

        if key == "open":
            if not document.exists():
                return f"{document} does not exist."
            result = run_applescript(
                f'tell application "{target}"\n  activate\n'
                f'  open POSIX file "{document}"\nend tell')
            return f"Opened {document.name} in {target}." if result.ok else \
                   f"Could not open it: {result.for_model()[:140]}"

        if key == "read":
            if not document.exists():
                return f"{document} does not exist."
            run_applescript(f'tell application "{target}" to open POSIX file "{document}"')
            result = run_applescript(
                f'tell application "{target}" to return body text of document 1',
                timeout=30)
            if not result.ok:
                return (f"Could not read it. {target} exposes body text for Pages "
                        f"documents; spreadsheets and decks need a different "
                        f"approach. ({result.for_model()[:100]})")
            text = result.output.strip()
            return (text[:3000] + "\n\n[truncated]") if len(text) > 3000 else \
                   (text or "The document is empty.")

        if key == "export_pdf":
            if not document.exists():
                return f"{document} does not exist."
            pdf = document.with_suffix(".pdf")
            if pdf.exists() and not require_confirmation(
                    "overwrites an existing PDF", f"overwrite {pdf.name}"):
                return "Refused by the owner — the existing PDF was left alone."
            result = run_applescript(
                f'tell application "{target}"\n'
                f'  open POSIX file "{document}"\n'
                f'  export document 1 to POSIX file "{pdf}" as PDF\n'
                f'end tell', timeout=90)
            return f"Exported to {pdf}." if result.ok else \
                   f"Could not export: {result.for_model()[:140]}"

        if key == "close":
            result = run_applescript(
                f'tell application "{target}" to close every document saving yes')
            return f"Closed and saved documents in {target}." if result.ok else \
                   f"Could not close: {result.for_model()[:120]}"

        return (f"Unknown action {action!r}. Choose one of: open, read, "
                f"export_pdf, close, list_open.")

    return _guarded("applescript",
                    {"document_control": action, "path": path, "app": app}, run)


# --------------------------------------------------------------------------- #
# Shortcuts
# --------------------------------------------------------------------------- #


@tool
def run_shortcut(action: str, name: str = "", input_path: str = "") -> str:
    """List or run a macOS Shortcut.

    Shortcuts reach apps that have no AppleScript support, and anything the
    owner builds in the Shortcuts app becomes available here automatically.

    Args:
        action: "list" to see available shortcuts, or "run" to run one.
        name: Shortcut name, required for "run".
        input_path: Optional file to pass to the shortcut as input.
    """
    def run() -> str:
        key = action.strip().lower()
        if key == "list":
            result = shell_tool.run("shortcuts list", timeout=20)
            names = result.output.strip()
            return f"Available shortcuts:\n{names}" if names else "No shortcuts found."

        if key != "run":
            return f"Unknown action {action!r}. Choose 'list' or 'run'."
        if not name.strip():
            return "Give the name of a shortcut to run."

        # A shortcut is arbitrary user-authored automation, so it is confirmed
        # rather than assumed safe.
        if not require_confirmation(f"runs the shortcut {name!r}",
                                    f"run_shortcut({name})"):
            return "Refused by the owner."

        command = f"shortcuts run {shlex.quote(name.strip())}"
        if input_path.strip():
            try:
                source = _safe_folder(input_path)
            except PathRefused as exc:
                return str(exc)
            command += f" -i {shlex.quote(str(source))}"

        result = shell_tool.run(command, timeout=120)
        return (f"Ran {name}. {result.output.strip()[:400]}" if result.ok
                else f"Shortcut failed: {result.output[:200]}")

    return _guarded("bash", {"run_shortcut": action, "name": name}, run)


#: Level 2 primitives.
APP_TOOLS = [media_control, browser_control, document_control, run_shortcut]

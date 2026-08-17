"""Level 1 primitives — native macOS operations.

Deterministic system, network and filesystem control. Free, fast, no key, and
mostly working while the screen is locked. Most everyday value lives here
(CAPABILITY.md §3).

## Why three tools and not fifteen

`lock_mac()`, `sleep_mac()`, `set_volume()`, `mute()` … as separate tools would
mean fifteen Swift proxy structs and a tool list long enough to crowd the
model's context. Instead each primitive takes a typed **action** field, so the
model picks an enum value and fills one or two arguments — measured to be the
thing it does reliably (8/8), as opposed to authoring syntax (1/5).

## Safety

Destructive actions call `require_confirmation` explicitly rather than relying
on `guard.classify`, which matches patterns in argument text and would see
nothing alarming in `{"action": "trash"}`. Paths are validated before use, so
`DENIED_READ_PATHS` is enforced by construction.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

from macman.agent.tools.schema import tool

from macman.agent.tools import shell as shell_tool
from macman.agent.tools.registry import _guarded, require_confirmation
from macman.agent.tools.typed import QUERY_TOOLS, PathRefused, _safe_folder

# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #

#: `pmset displaysleepnow` only sleeps the display; whether that locks depends
#: on the "require password after sleep" delay. CGSession locks outright.
_LOCK_COMMAND = (
    '"/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/'
    'CGSession" -suspend'
)

_SYSTEM_ACTIONS = {
    "lock": (_LOCK_COMMAND, None),
    "sleep": ("pmset sleepnow", None),
    "display_off": ("pmset displaysleepnow", None),
    "restart": ('osascript -e \'tell app "System Events" to restart\'',
                "restarts the Mac, closing everything"),
    "shutdown": ('osascript -e \'tell app "System Events" to shut down\'',
                 "shuts the Mac down"),
    "mute": ('osascript -e "set volume with output muted"', None),
    "unmute": ('osascript -e "set volume without output muted"', None),
}


@tool
def system_control(action: str, value: int = -1, name: str = "") -> str:
    """Control this Mac: lock, sleep, volume, brightness, Wi-Fi, Bluetooth.

    Use for any request about the Mac itself rather than about files or apps.

    Args:
        action: One of "lock", "sleep", "display_off", "restart", "shutdown",
            "mute", "unmute", "volume", "brightness", "wifi_on", "wifi_off",
            "wifi_status", "wifi_list", "wifi_join", "bluetooth_on",
            "bluetooth_off", "bluetooth_status".
        value: Required for "volume" and "brightness" — a percentage from 0
            to 100. Ignored otherwise.
        name: Network name, required only for "wifi_join".

    Note:
        Wi-Fi and Bluetooth were once a separate `network_control` tool. It
        scored 0/6 on selection — every naturally-phrased Wi-Fi request went to
        a system tool instead, and rewriting both descriptions to say so
        explicitly barely moved it (2/6). Wi-Fi *is* a system setting in the
        way people think about it, so the tools were merged rather than
        continuing to argue with the model about a distinction it does not
        make. See RELIABILITY.md.
    """
    def run() -> str:
        key = action.strip().lower()

        if key.startswith(("wifi", "bluetooth")):
            return _network_action(key, name)

        if key in {"volume", "brightness"}:
            if not 0 <= value <= 100:
                return f"Give a {key} between 0 and 100."
            if key == "volume":
                result = shell_tool.run(
                    f'osascript -e "set volume output volume {value}"', timeout=10)
                return (f"Volume set to {value}%." if result.ok
                        else f"Could not set volume: {result.output[:100]}")
            # No supported CLI for brightness; System Events key codes are the
            # portable route, and they need Accessibility permission.
            presses = round(value / 100 * 16)
            script = (
                'tell application "System Events"\n'
                '  repeat 16 times\n    key code 145\n  end repeat\n'
                f'  repeat {presses} times\n    key code 144\n  end repeat\n'
                'end tell'
            )
            result = shell_tool.run(f"osascript -e {shlex.quote(script)}", timeout=20)
            return (f"Brightness set to about {value}%." if result.ok
                    else "Could not change brightness — this needs Accessibility "
                         "permission.")

        entry = _SYSTEM_ACTIONS.get(key)
        if entry is None:
            options = ", ".join(sorted(_SYSTEM_ACTIONS) + [
                "volume", "brightness", "wifi_on", "wifi_off", "wifi_status",
                "wifi_list", "wifi_join", "bluetooth_on", "bluetooth_off",
                "bluetooth_status"])
            return f"Unknown action {action!r}. Choose one of: {options}."

        command, danger = entry
        if danger and not require_confirmation(danger, f"system_control({key})"):
            return f"Refused by the owner: this {danger}."

        result = shell_tool.run(command, timeout=20)
        return f"Done: {key}." if result.ok else f"Failed: {result.output[:120]}"

    return _guarded("bash", {"system_control": action, "value": value}, run)


# --------------------------------------------------------------------------- #
# Network
# --------------------------------------------------------------------------- #


def _wifi_device() -> str:
    """Find the Wi-Fi interface rather than assuming `en0`.

    It is `en1` on some Macs, and hardcoding `en0` silently controls the wrong
    interface — an Ethernet adapter, say.
    """
    result = shell_tool.run(
        "networksetup -listallhardwareports | awk '/Wi-Fi|AirPort/{getline; print $2}'",
        timeout=15,
    )
    return (result.output.strip().splitlines() or ["en0"])[0]


def _network_action(key: str, name: str = "") -> str:
    """Wi-Fi and Bluetooth, reached through `system_control`.

    No longer a tool of its own — see the note on `system_control`. Not
    separately guarded either: the only caller is already inside `_guarded`.
    """
    if key.startswith("wifi"):
        device = _wifi_device()
        if key == "wifi_on":
            result = shell_tool.run(f"networksetup -setairportpower {device} on")
            return "Wi-Fi on." if result.ok else f"Failed: {result.output[:100]}"
        if key == "wifi_off":
            if not require_confirmation("turns off Wi-Fi, which will end this "
                                        "session if you are connecting over it",
                                        "network_control(wifi_off)"):
                return "Refused by the owner."
            result = shell_tool.run(f"networksetup -setairportpower {device} off")
            return "Wi-Fi off." if result.ok else f"Failed: {result.output[:100]}"
        if key == "wifi_status":
            result = shell_tool.run(f"networksetup -getairportnetwork {device}")
            return result.output.strip() or "Could not read Wi-Fi status."
        if key == "wifi_list":
            result = shell_tool.run(
                f"networksetup -listpreferredwirelessnetworks {device}")
            return result.output.strip() or "No saved networks."
        if key == "wifi_join":
            if not name.strip():
                return "Give the name of a saved network to join."
            # Deliberately no password argument: MACman does not accept
            # credentials, so only already-saved networks can be joined.
            result = shell_tool.run(
                f"networksetup -setairportnetwork {device} {shlex.quote(name.strip())}",
                timeout=30)
            return (f"Joined {name}." if result.ok and "not" not in result.output.lower()
                    else f"Could not join {name}. It must already be saved on this "
                         f"Mac — MACman cannot enter a password.")

    if key.startswith("bluetooth"):
        if shutil.which("blueutil") is None:
            return ("Bluetooth control needs blueutil, which isn't installed. "
                    "Install it with: brew install blueutil")
        # blueutil needs its own Bluetooth permission and fails loudly
        # without it. Treating any non-"1" output as "off" would report a
        # permission error as a confident fact about the hardware.
        def bluetooth_error(output: str) -> str | None:
            lowered = output.lower()
            if "access" in lowered or "abort" in lowered:
                return ("Bluetooth control needs permission. Grant it under "
                        "System Settings → Privacy & Security → Bluetooth "
                        "for the app running MACman, then try again.")
            return None

        if key == "bluetooth_status":
            result = shell_tool.run("blueutil --power")
            state = result.output.strip()
            if (problem := bluetooth_error(result.output)) is not None:
                return problem
            if state not in {"0", "1"}:
                return f"Could not read Bluetooth state: {result.output[:100]}"
            return f"Bluetooth is {'on' if state == '1' else 'off'}."

        state = "1" if key == "bluetooth_on" else "0"
        result = shell_tool.run(f"blueutil --power {state}")
        if (problem := bluetooth_error(result.output)) is not None:
            return problem
        return (f"Bluetooth {'on' if state == '1' else 'off'}." if result.ok
                else f"Failed: {result.output[:100]}")

    return (f"Unknown action {action!r}. Choose one of: wifi_on, wifi_off, "
            f"wifi_status, wifi_list, wifi_join, bluetooth_on, bluetooth_off, "
            f"bluetooth_status.")



# --------------------------------------------------------------------------- #
# Filesystem
# --------------------------------------------------------------------------- #


@tool
def file_operation(action: str, source: str, destination: str = "") -> str:
    """Move, copy, rename, trash, compress files, or create a folder.

    Args:
        action: One of "move", "copy", "rename", "trash", "compress",
            "make_folder".
        source: The file or folder to act on, e.g. "~/Downloads/report.pdf".
            For "make_folder", the folder to create.
        destination: Where it goes. A folder for "move"/"copy", the new name
            for "rename", the archive path for "compress". Unused otherwise.
    """
    def run() -> str:
        key = action.strip().lower()
        try:
            src = _safe_folder(source)
        except PathRefused as exc:
            return str(exc)

        if key == "make_folder":
            if src.exists():
                return f"{src} already exists."
            src.mkdir(parents=True)
            return f"Created {src}."

        if not src.exists():
            return f"{src} does not exist."

        if key == "trash":
            if not require_confirmation("moves files to the Trash", f"trash {src}"):
                return "Refused by the owner."
            # Finder's delete puts things in the Trash, where they can be
            # recovered; `rm` would not.
            script = (f'tell application "Finder" to delete POSIX file '
                      f'"{src}"')
            result = shell_tool.run(f"osascript -e {shlex.quote(script)}", timeout=30)
            return f"Moved {src.name} to the Trash." if result.ok else \
                   f"Could not trash it: {result.output[:120]}"

        if key == "compress":
            archive = (Path(destination).expanduser() if destination
                       else src.with_suffix(src.suffix + ".zip"))
            result = shell_tool.run(
                f"ditto -c -k --sequesterRsrc --keepParent "
                f"{shlex.quote(str(src))} {shlex.quote(str(archive))}", timeout=120)
            return f"Compressed to {archive}." if result.ok else \
                   f"Could not compress: {result.output[:120]}"

        if not destination.strip():
            return f"{key} needs a destination."

        try:
            dst = _safe_folder(destination)
        except PathRefused as exc:
            return str(exc)

        if key == "rename":
            target = src.parent / Path(destination).name
            if target.exists():
                return f"{target.name} already exists."
            src.rename(target)
            return f"Renamed to {target.name}."

        if key in {"move", "copy"}:
            if dst.is_dir():
                target = dst / src.name
            else:
                target = dst
            if target.exists():
                return f"{target} already exists — nothing was overwritten."
            target.parent.mkdir(parents=True, exist_ok=True)
            if key == "move":
                shutil.move(str(src), str(target))
                return f"Moved {src.name} to {target.parent}."
            if src.is_dir():
                shutil.copytree(src, target)
            else:
                shutil.copy2(src, target)
            return f"Copied {src.name} to {target.parent}."

        return (f"Unknown action {action!r}. Choose one of: move, copy, rename, "
                f"trash, compress, make_folder.")

    return _guarded("bash",
                    {"file_operation": action, "source": source,
                     "destination": destination}, run)


#: Level 1 primitives.
ACTION_TOOLS = [system_control, file_operation]

#: The full tool set handed to the on-device model — queries, Level 1
#: actions, and Level 2 application control.
#: Assembled here rather than in `typed.py` because this module already
#: depends on that one; the reverse would be circular.
from macman.agent.tools.apps import APP_TOOLS  # noqa: E402
from macman.agent.tools.personal import PERSONAL_TOOLS  # noqa: E402
from macman.agent.tools.dev import DEV_TOOLS  # noqa: E402

LOCAL_TOOLS = (QUERY_TOOLS + ACTION_TOOLS + APP_TOOLS
               + PERSONAL_TOOLS + DEV_TOOLS)


def local_tool_by_name(name: str):
    return next((tool for tool in LOCAL_TOOLS if tool.to_dict()["name"] == name), None)

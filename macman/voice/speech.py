"""Speech in and out, via the `macman-speech` helper.

Both halves run on-device: `SFSpeechRecognizer` in on-device mode for
transcription, `AVSpeechSynthesizer` for the reply. Nothing is uploaded, and it
works with no network — the same principle as the rest of the local engine.

The `device` argument on `say` is what v3 needs: speaking into BlackHole is how
MACman talks *into* a FaceTime call, since macOS offers no way to inject audio
into a microphone. Leaving it unset speaks through the normal output.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from macman import config

_HELPER_CANDIDATES = (
    config.HELPERS_BIN / "macman-speech",
    config.HELPERS_BIN.parent / "debug" / "macman-speech",
)

#: Backstop only — `listen` normally ends on a pause in speech.
LISTEN_TIMEOUT_SECONDS = 30

#: Long enough for a paragraph read aloud.
SPEAK_TIMEOUT_SECONDS = 180


class SpeechUnavailable(RuntimeError):
    """Raised when the speech helper is missing or cannot be used."""


def helper_path() -> Path | None:
    return next((path for path in _HELPER_CANDIDATES if path.exists()), None)


@dataclass(frozen=True)
class SpeechStatus:
    available: bool
    detail: str
    on_device: bool = False
    output_devices: tuple[str, ...] = ()

    def has_device(self, name: str) -> bool:
        return any(name.casefold() in device.casefold()
                   for device in self.output_devices)


def status() -> SpeechStatus:
    """Whether speech is usable, and which output devices exist."""
    helper = helper_path()
    if helper is None:
        return SpeechStatus(False,
                            "macman-speech not built — run `swift build` in helpers/")
    try:
        result = subprocess.run([str(helper), "check"], capture_output=True,
                                text=True, timeout=20)
        report = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return SpeechStatus(False, f"speech helper did not respond ({exc})")

    if not report.get("recogniserAvailable"):
        return SpeechStatus(False, "the speech recogniser is unavailable")

    return SpeechStatus(
        True,
        "ready" if report.get("onDeviceSupported") else
        "ready (on-device transcription unsupported — audio would leave the Mac)",
        bool(report.get("onDeviceSupported")),
        tuple(report.get("outputDevices") or ()),
    )


def listen(max_seconds: int = 20, silence_seconds: float = 1.2) -> str | None:
    """Transcribe one spoken turn. Returns None when nothing was heard.

    Ends on a pause rather than a fixed window, so a short answer doesn't leave
    the caller waiting and a long one isn't truncated mid-sentence.
    """
    helper = helper_path()
    if helper is None:
        raise SpeechUnavailable("macman-speech is not built.")

    try:
        result = subprocess.run(
            [str(helper), "listen", "--seconds", str(max_seconds),
             "--silence", str(silence_seconds)],
            capture_output=True, text=True,
            timeout=max_seconds + LISTEN_TIMEOUT_SECONDS,
        )
        payload = json.loads(result.stdout or "{}")
    except subprocess.TimeoutExpired:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise SpeechUnavailable(f"listening failed: {exc}") from exc

    if not payload.get("ok"):
        return None            # "nothing heard" is ordinary, not an error
    return (payload.get("text") or "").strip() or None


def say(text: str, device: str | None = None) -> bool:
    """Speak text aloud, optionally through a named output device."""
    helper = helper_path()
    if helper is None:
        raise SpeechUnavailable("macman-speech is not built.")
    if not text.strip():
        return False

    command = [str(helper), "say", "--text", text]
    if device:
        command += ["--device", device]

    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=SPEAK_TIMEOUT_SECONDS)
        return bool(json.loads(result.stdout or "{}").get("ok"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False

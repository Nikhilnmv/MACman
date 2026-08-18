#!/usr/bin/env python3
"""Experiment: does on-device transcription survive call-quality audio?

    .venv/bin/python tests/tasks/call_audio.py

Clean-microphone transcription measured perfect, and that result was used to
justify the FaceTime plan. It is also the wrong measurement. A FaceTime call is
a *different signal*: narrowband, lossily compressed at low bitrate, with
dropouts. If accuracy collapses there, voice control over a call does not work
and no amount of downstream engineering fixes it — so this is worth knowing
before the call driver is written, not after.

The prediction on record was that this would fail.

## Method

Speech is synthesised with macOS `say`, giving an exact ground-truth transcript
— the thing a microphone test can never have. Each utterance is then encoded
with **AAC-ELD**, the codec FaceTime actually uses, via Apple's own `afconvert`,
decoded back, and transcribed by the same on-device recogniser MACman uses.
Score is word error rate against the known text.

Using Apple's real encoder matters. The first version of this reached for
ffmpeg and plain AAC-LC, which would have measured a codec FaceTime does not
use — and ffmpeg turned out to be broken on this machine anyway, which is how
the better path got found.

## What this is not

Synthetic speech is *cleaner* than human speech: no accent, no hesitation, no
room noise, consistent pace. **These numbers are an optimistic bound.** A live
call also carries adaptive bitrate, packet loss and jitter-buffer artefacts
that an offline encode does not reproduce. A real call remains the real test;
this exists to find out whether it is worth setting one up.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEECH = ROOT / "helpers/.build/arm64-apple-macosx/debug/macman-speech"

#: Things someone would actually say to MACman down a call. Chosen to include
#: the vocabulary that matters — file types, app names, numbers — since an
#: error on "PDF" costs more than an error on "the".
UTTERANCES = [
    "How many PDF files are in my Downloads folder",
    "Lock my Mac right now please",
    "What is in my Documents folder",
    "How many unread emails do I have",
    "Remind me to call the bank tomorrow at nine",
    "Open Safari and check the battery level",
]


@dataclass
class Condition:
    name: str
    detail: str
    #: AAC-ELD bitrate in bits per second. None means no compression at all.
    bitrate: int | None
    #: Sample rate the recogniser finally receives.
    rate: int = 16000


#: `aace` is AAC-ELD — Apple's Enhanced Low Delay codec, the one FaceTime uses.
#: It rejects an 8 kHz sample rate, so the narrowband case is produced by
#: downsampling after decode rather than by encoding at 8 kHz.
CONDITIONS = [
    Condition("clean 48k", "uncompressed — the published result", None, 48000),
    Condition("wideband 16k", "16 kHz, uncompressed", None, 16000),
    Condition("AAC-ELD 48k", "16 kHz, AAC-ELD 48 kbps — good call", 48000),
    Condition("AAC-ELD 32k", "16 kHz, AAC-ELD 32 kbps — typical call", 32000),
    Condition("AAC-ELD 24k", "16 kHz, AAC-ELD 24 kbps — mediocre call", 24000),
    Condition("AAC-ELD 16k", "16 kHz, AAC-ELD 16 kbps — poor connection", 16000),
    Condition("narrowband 8k", "AAC-ELD 16 kbps then downsampled to 8 kHz",
              16000, 8000),
]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over words, divided by reference length.

    Case and punctuation are stripped: the recogniser writing "Mac" for "mac"
    is not an error anyone cares about, and counting it would flatter or
    penalise conditions arbitrarily.
    """
    ref = [w.strip(".,?!'\"").lower() for w in reference.split()]
    hyp = [w.strip(".,?!'\"").lower() for w in hypothesis.split()]
    if not ref:
        return 0.0

    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        current = [i]
        for j, h in enumerate(hyp, start=1):
            current.append(min(
                previous[j] + 1,          # deletion
                current[j - 1] + 1,       # insertion
                previous[j - 1] + (r != h)))  # substitution
        previous = current
    return previous[len(hyp)] / len(ref)


def _afconvert(args: list[str]) -> bool:
    result = subprocess.run(["afconvert", *args], capture_output=True, text=True)
    return result.returncode == 0


def synthesise(text: str, destination: Path) -> bool:
    """Render text to audio with macOS `say`, as 48 kHz mono WAV."""
    aiff = destination.with_suffix(".aiff")
    made = subprocess.run(["say", "-o", str(aiff), text],
                          capture_output=True, text=True)
    if made.returncode != 0 or not aiff.exists():
        return False
    ok = _afconvert(["-f", "WAVE", "-d", "LEI16@48000", "-c", "1",
                     str(aiff), str(destination)])
    aiff.unlink(missing_ok=True)
    return ok and destination.exists()


def degrade(source: Path, destination: Path, condition: Condition) -> bool:
    """Put clean audio through the condition and hand back decoded WAV.

    Every condition ends as WAV at its target rate, so the recogniser always
    receives the same container and only the *damage* differs.
    """
    if condition.bitrate is None:
        return _afconvert(["-f", "WAVE", "-d", f"LEI16@{condition.rate}",
                           "-c", "1", str(source), str(destination)]) \
            and destination.exists()

    encoded = destination.with_suffix(".m4a")
    # AAC-ELD encodes at 16 kHz; the narrowband case reduces further on decode.
    if not _afconvert(["-f", "m4af", "-d", "aace@16000",
                       "-b", str(condition.bitrate), "-c", "1",
                       str(source), str(encoded)]):
        return False
    ok = _afconvert(["-f", "WAVE", "-d", f"LEI16@{condition.rate}", "-c", "1",
                     str(encoded), str(destination)])
    encoded.unlink(missing_ok=True)
    return ok and destination.exists()


def transcribe(path: Path) -> tuple[str | None, bool, str]:
    """Returns (text, on_device, error).

    Reports *why* it failed rather than returning a bare None. An earlier
    version swallowed a crash into an empty string and printed 42 blank
    ERROR lines, which said nothing about a SIGABRT from TCC.
    """
    result = subprocess.run([str(SPEECH), "transcribe", "--file", str(path)],
                            capture_output=True, text=True, timeout=200)
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        detail = (result.stderr or result.stdout).strip()
        if result.returncode == 134:
            detail = ("helper was killed by macOS (SIGABRT) — a privacy "
                      "permission was requested without a usage description")
        return None, False, detail or f"no output, exit {result.returncode}"
    if not payload.get("ok"):
        return None, payload.get("onDevice", False), payload.get("error", "?")
    return payload.get("text", ""), payload.get("onDevice", False), ""


def permission_blocked() -> str | None:
    """Whether speech permission stops this running at all.

    Checked once, up front. Discovering it per-utterance produces dozens of
    identical failures and buries the one fact that matters.
    """
    result = subprocess.run([str(SPEECH), "check"], capture_output=True,
                            text=True, timeout=60)
    try:
        status = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return f"could not read permission state: {result.stderr[:80]}"
    if status.get("speechRecognition") != "authorized":
        return (f"speech recognition is '{status.get('speechRecognition')}'.\n"
                "  Permission is attributed to the app that launched this "
                "process.\n"
                "  Run this experiment from Terminal, where it has been "
                "granted:\n\n"
                "    cd " + str(ROOT) + " \\\n"
                "      && .venv/bin/python tests/tasks/call_audio.py")
    return None


def main() -> int:
    print("Call-audio transcription — does accuracy survive compression?\n")

    if not SPEECH.exists():
        print("  Build the helper first: cd helpers && swift build")
        return 2
    if not shutil.which("afconvert"):
        print("  afconvert is missing — it ships with macOS; this is unexpected.")
        return 2
    if (blocked := permission_blocked()) is not None:
        print(f"  BLOCKED: {blocked}")
        return 2

    on_device_seen: set[bool] = set()
    summary: list[tuple[str, float, int, int]] = []

    with tempfile.TemporaryDirectory(prefix="macman-callaudio-") as tmp:
        workspace = Path(tmp)

        print("── Synthesising ground-truth speech")
        sources: list[tuple[str, Path]] = []
        for index, text in enumerate(UTTERANCES):
            source = workspace / f"src{index}.wav"
            if synthesise(text, source):
                sources.append((text, source))
            else:
                print(f"   skipped (synthesis failed): {text[:40]}")
        print(f"   {len(sources)} utterances ready\n")

        if not sources:
            print("  Nothing to measure.")
            return 1

        for condition in CONDITIONS:
            rates: list[float] = []
            failures = 0
            print(f"── {condition.name}  ({condition.detail})")

            for index, (text, source) in enumerate(sources):
                degraded = workspace / f"{condition.name.replace(' ', '_')}{index}.wav"
                if not degrade(source, degraded, condition):
                    failures += 1
                    continue

                heard, on_device, error = transcribe(degraded)
                on_device_seen.add(on_device)
                if heard is None:
                    failures += 1
                    print(f"   ERROR  {error[:60]}")
                    continue

                rate = word_error_rate(text, heard)
                rates.append(rate)
                mark = "ok " if rate == 0 else f"{rate:.0%}"
                print(f"   [{mark:>4}] {heard[:62]}")
                if rate > 0:
                    print(f"          want: {text[:62]}")

            average = sum(rates) / len(rates) if rates else 1.0
            perfect = sum(1 for r in rates if r == 0)
            summary.append((condition.name, average, perfect, len(rates)))
            print(f"   → mean WER {average:.1%}, {perfect}/{len(rates)} exact"
                  f"{f', {failures} failed' if failures else ''}\n")

    print("─" * 70)
    print(f"  {'condition':<20} {'mean WER':>10} {'exact':>10}")
    for name, average, perfect, total in summary:
        print(f"  {name:<20} {average:>9.1%} {f'{perfect}/{total}':>10}")

    if on_device_seen == {True}:
        print("\n  All transcription ran on-device.")
    elif True in on_device_seen:
        print("\n  WARNING: some transcription was NOT on-device.")
    else:
        print("\n  WARNING: on-device recognition unavailable — audio may have "
              "been sent to Apple.")

    print("\n  Synthetic speech is cleaner than human speech; treat these as an"
          "\n  optimistic bound. A live call is still the real test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

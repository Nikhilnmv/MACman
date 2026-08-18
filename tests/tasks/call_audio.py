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
import math
import random
import shutil
import subprocess
import sys
import tempfile
import wave
import zlib
from array import array
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
    #: Signal-to-noise ratio in dB for added background noise. None = silent room.
    snr_db: float | None = None
    #: Fraction of 20 ms packets dropped, as a proportion.
    loss: float = 0.0


#: `aace` is AAC-ELD — Apple's Enhanced Low Delay codec, the one FaceTime uses.
#: It rejects an 8 kHz sample rate, so the narrowband case is produced by
#: downsampling after decode rather than by encoding at 8 kHz.
#:
#: **The codec is the least damaging thing about a call.** A first version of
#: this experiment varied only bitrate and sample rate, and every condition
#: scored 0.0% WER — including 8 kHz at 16 kbps. Identical scores across a 6×
#: bitrate range do not show a robust recogniser; they show a test that never
#: got hard enough to discriminate. What actually degrades a call is noise,
#: packet loss and dropouts, so those are varied here too.
CONDITIONS = [
    # Baselines — kept so the comparison is visible rather than assumed.
    Condition("clean 48k", "uncompressed reference", None, 48000),
    Condition("AAC-ELD 24k", "16 kHz, 24 kbps — codec only", 24000),
    Condition("narrowband 8k", "24 kbps then downsampled to 8 kHz", 24000, 8000),

    # Background noise: a café, a street, someone else's room.
    Condition("noise 20dB", "24 kbps + mild background noise", 24000,
              snr_db=20),
    Condition("noise 10dB", "24 kbps + loud background noise", 24000,
              snr_db=10),
    Condition("noise 5dB", "24 kbps + severe background noise", 24000,
              snr_db=5),

    # Packet loss: the thing that actually makes calls unintelligible.
    Condition("loss 2%", "24 kbps + 2% of 20 ms packets dropped", 24000,
              loss=0.02),
    Condition("loss 5%", "24 kbps + 5% dropped", 24000, loss=0.05),
    Condition("loss 10%", "24 kbps + 10% dropped", 24000, loss=0.10),

    # What a genuinely bad call looks like.
    Condition("bad call", "24 kbps + 10 dB noise + 5% loss", 24000,
              snr_db=10, loss=0.05),
    Condition("terrible call", "16 kbps + 5 dB noise + 10% loss, 8 kHz",
              16000, rate=8000, snr_db=5, loss=0.10),
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


# --------------------------------------------------------------------------- #
# Impairments
#
# Applied to 16-bit PCM with the standard library, so running this experiment
# needs nothing that MACman itself does not already install.
# --------------------------------------------------------------------------- #


def _read_pcm(path: Path) -> tuple[array, int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM")
        rate = handle.getframerate()
        samples = array("h")
        samples.frombytes(handle.readframes(handle.getnframes()))
    return samples, rate


def _write_pcm(path: Path, samples: array, rate: int) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())


def add_noise(samples: array, snr_db: float, rng: random.Random) -> array:
    """Add white noise at a given signal-to-noise ratio.

    White noise is harsher on a recogniser than real room noise, which is
    mostly low-frequency. Treating this as *pessimistic* is the intent: the
    previous version of this test erred optimistic and learned nothing.
    """
    if not samples:
        return samples
    power = sum(float(s) * s for s in samples) / len(samples)
    if power <= 0:
        return samples
    # amplitude ratio = 10^(-SNR/20), applied to RMS
    noise_rms = math.sqrt(power) * (10 ** (-snr_db / 20))
    out = array("h")
    for sample in samples:
        noisy = sample + rng.gauss(0, noise_rms)
        out.append(max(-32768, min(32767, int(noisy))))
    return out


def drop_packets(samples: array, rate: int, loss: float,
                 rng: random.Random, packet_ms: int = 20) -> array:
    """Zero whole packets at random, the way a lossy connection does.

    Real VoIP conceals loss by interpolating; zeroing is harsher. A dropout is
    silence in the middle of a word, which is exactly the failure a codec
    bitrate test cannot produce.
    """
    if loss <= 0 or not samples:
        return samples
    packet = max(1, int(rate * packet_ms / 1000))
    out = array("h", samples)
    for start in range(0, len(out), packet):
        if rng.random() < loss:
            for index in range(start, min(start + packet, len(out))):
                out[index] = 0
    return out


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


def degrade(source: Path, destination: Path, condition: Condition,
            rng: random.Random) -> bool:
    """Put clean audio through the condition and hand back decoded WAV.

    Order:

    1. **Resample to the final rate first.**
    2. **Add noise there**, so the labelled SNR is the SNR the recogniser
       actually receives.
    3. **Encode and decode** with AAC-ELD, which the noise passes through as a
       microphone's noise would.
    4. **Packet loss last**, because loss happens on the wire.

    Step 1 is the correction. Adding wideband noise at 48 kHz *before*
    resampling — the obvious order, and what this did originally — spreads
    noise energy up to 24 kHz, and resampling then discards everything above
    the new Nyquist along with most of the noise. Measured on this machine:
    a nominal 5 dB SNR arrived as **10.5 dB** at 16 kHz and **13.7 dB** at
    8 kHz.

    The result was an experiment where degrading harder made the audio
    cleaner. "terrible call" at 8 kHz scored *better* than plain 5 dB noise at
    16 kHz, because downsampling had removed more of the noise than it removed
    of the speech. Adding damage cannot improve accuracy; that contradiction
    is what exposed it.

    Band-limiting first also models real noise better. Room and line noise sit
    in the speech band; they are not white to 24 kHz.
    """
    working = destination.with_name(destination.stem + "_work.wav")

    # 1. Resample to the final rate before anything else touches the signal.
    if not _afconvert(["-f", "WAVE", "-d", f"LEI16@{condition.rate}", "-c", "1",
                       str(source), str(working)]):
        return False

    # 2. Noise, now in-band by construction.
    if condition.snr_db is not None:
        samples, rate = _read_pcm(working)
        _write_pcm(working, add_noise(samples, condition.snr_db, rng), rate)

    # 3. Codec. AAC-ELD encodes at 16 kHz; decode returns to the target rate.
    if condition.bitrate is None:
        shutil.copy(working, destination)
        ok = True
    else:
        encoded = destination.with_suffix(".m4a")
        if not _afconvert(["-f", "m4af", "-d", "aace@16000",
                           "-b", str(condition.bitrate), "-c", "1",
                           str(working), str(encoded)]):
            working.unlink(missing_ok=True)
            return False
        ok = _afconvert(["-f", "WAVE", "-d", f"LEI16@{condition.rate}",
                         "-c", "1", str(encoded), str(destination)])
        encoded.unlink(missing_ok=True)

    working.unlink(missing_ok=True)
    if not (ok and destination.exists()):
        return False

    # 4. Loss, on the decoded stream.
    if condition.loss > 0:
        samples, rate = _read_pcm(destination)
        _write_pcm(destination,
                   drop_packets(samples, rate, condition.loss, rng), rate)
    return True


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


def _rms(samples) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(float(s) * s for s in samples) / len(samples))


def calibrate(source: Path, workspace: Path) -> list[str]:
    """Confirm each condition delivers the impairment it advertises.

    This exists because it did not, and nobody noticed. Nominal 5 dB SNR was
    arriving as 10.5 dB at 16 kHz and 13.7 dB at 8 kHz, so a table of
    scores was labelled with numbers that were wrong by up to 9 dB.

    A measurement is only worth as much as the check that it measured the
    thing on the label.
    """
    lines = []
    for condition in CONDITIONS:
        if condition.snr_db is None:
            continue
        rng = random.Random(zlib.crc32(f"calib:{condition.name}".encode()))

        reference = workspace / f"ref_{condition.rate}.wav"
        if not reference.exists():
            _afconvert(["-f", "WAVE", "-d", f"LEI16@{condition.rate}", "-c", "1",
                        str(source), str(reference)])
        clean, _ = _read_pcm(reference)

        noisy = add_noise(clean, condition.snr_db, rng)
        residual = [n - c for n, c in zip(noisy, clean)]
        delivered = 20 * math.log10(_rms(clean) / max(1e-9, _rms(residual)))
        drift = delivered - condition.snr_db
        flag = "" if abs(drift) < 1.0 else f"   ⚠ off by {drift:+.1f} dB"
        lines.append(f"   {condition.name:<16} want {condition.snr_db:>4.0f} dB"
                     f"   got {delivered:>5.1f} dB{flag}")
    return lines


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

        print("── Calibration: does each condition deliver what it claims?")
        for line in calibrate(sources[0][1], workspace):
            print(line)
        print()

        for condition in CONDITIONS:
            rates: list[float] = []
            failures = 0
            print(f"── {condition.name}  ({condition.detail})")

            for index, (text, source) in enumerate(sources):
                degraded = workspace / f"{condition.name.replace(' ', '_')}{index}.wav"
                # Seeded per (condition, utterance) rather than from one shared
                # stream: with a shared generator each condition draws different
                # noise depending on how many draws preceded it, so two
                # conditions cannot be compared without that confound.
                #
                # crc32, not hash() — string hashing is randomised per process,
                # which would make runs silently unreproducible.
                rng = random.Random(
                    zlib.crc32(f"{condition.name}:{index}".encode()))
                if not degrade(source, degraded, condition, rng):
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

    # A test whose hardest condition scores like its easiest has not measured
    # robustness. Saying so out loud, because the first version of this
    # experiment returned 0.0% everywhere and that was mistaken for good news.
    scores = {round(average, 4) for _, average, _, _ in summary}
    if len(scores) == 1:
        print("\n  ⚠ Every condition scored identically. That is NOT evidence of")
        print("    robustness — it means nothing here was hard enough to")
        print("    discriminate. Make the conditions worse, or use real speech,")
        print("    before treating this as a pass.")
    else:
        worst = max(summary, key=lambda row: row[1])
        print(f"\n  Degradation is visible: worst condition is '{worst[0]}' at "
              f"{worst[1]:.1%} WER.")
        print("  The test discriminates, so the numbers above mean something.")

    print("\n  Synthetic speech is cleaner than human speech; treat these as an"
          "\n  optimistic bound. A live call is still the real test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

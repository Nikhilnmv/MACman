#!/usr/bin/env python3
"""Can a spoken TOTP code be recognised — and never mistaken for a task?

    .venv/bin/python tests/tasks/spoken_code.py

Two failures matter, in opposite directions, and both have a security
consequence:

* **A code not recognised** — auth over FaceTime cannot work at all, and worse,
  the utterance falls through to the engine as *task text*. That is a
  credential handed to a model, the leak already fixed once for iMessage.
* **A task mistaken for a code** — "delete file 123456" swallowed by the auth
  gate instead of being run, or worse, treated as a credential.

Pure string tests; no audio and no permissions needed. The recogniser's actual
output for spoken digits is a separate question, checked by `--audio` (which
does need Speech Recognition, so run it from Terminal).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from macman.voice.digits import spoken_digits  # noqa: E402

SPEECH = ROOT / "helpers/.build/release/macman-speech"

#: (utterance, expected). None means "this is not a code".
CASES: list[tuple[str, str | None]] = [
    # Plain, as a recogniser usually renders a digit string.
    ("482913", "482913"),
    ("482 913", "482913"),
    ("482-913", "482913"),

    # Read out digit by digit, which is how people say codes.
    ("four eight two nine one three", "482913"),
    ("four, eight, two, nine, one, three", "482913"),
    ("zero zero one two three four", "001234"),

    # Zero has three spoken forms and all three appear in transcripts.
    # A code starting with zero is the case most likely to be mishandled, and
    # one in ten codes starts with one.
    ("oh four eight two nine one", "048291"),
    ("oh oh one two three four", "001234"),

    # Grouped in pairs or threes, which is also natural.
    ("forty eight twenty nine thirteen", "482913"),
    ("forty-eight twenty-nine thirteen", "482913"),

    # Framing words people add without thinking.
    ("my code is 482913", "482913"),
    ("the code is four eight two nine one three", "482913"),

    # NOT codes. Every one of these must fall through to the engine.
    ("delete file 123456", None),
    ("how many PDF files are in my Downloads folder", None),
    ("lock my Mac", None),
    ("remind me to call the bank at nine", None),
    ("open Safari", None),
    ("", None),
    ("   ", None),

    # Read as one cardinal number. Nobody says a code this way, but the
    # recogniser produces it — on an Indian-region Mac, "482913" spoken as a
    # number transcribes as "Four lakh 82,913". Number transcription is
    # locale-dependent, so the magnitude words are handled rather than assumed
    # away.
    ("Four lakh 82,913", "482913"),
    ("four hundred eighty two thousand nine hundred thirteen", "482913"),
    ("482,913", "482913"),

    # Wrong length is not a code.
    ("four eight two", None),
    ("1234567", None),
    ("12345", None),

    # Magnitude words inside an actual request must stay a request.
    ("transfer four hundred thousand to savings", None),
    ("remind me about the four hundred pound invoice", None),
]


def string_cases() -> tuple[int, int]:
    print("── Recognising a code, and refusing everything else")
    passed = failed = 0
    for utterance, expected in CASES:
        actual = spoken_digits(utterance)
        ok = actual == expected
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        mark = "ok  " if ok else "FAIL"
        shown = f"{utterance!r}"[:44]
        print(f"   [{mark}] {shown:<46} → {actual!r}")
        if not ok:
            print(f"          expected {expected!r}")
    return passed, failed


def audio_cases() -> tuple[int, int]:
    """What does the recogniser actually emit for a spoken code?

    The string tests above cover forms a recogniser *might* produce. This
    checks which one it does produce, because guessing that is how the gap
    appeared in the first place.
    """
    print("\n── What the recogniser really returns for a spoken code")
    if not SPEECH.exists():
        print("   SKIP — build helpers first")
        return 0, 0

    spoken = [
        ("digit by digit", "four eight two nine one three"),
        ("as a number", "482913"),
        ("in pairs", "forty eight twenty nine thirteen"),
    ]
    passed = failed = 0
    with tempfile.TemporaryDirectory(prefix="macman-code-") as tmp:
        for label, phrase in spoken:
            aiff = Path(tmp) / "c.aiff"
            wav = Path(tmp) / "c.wav"
            subprocess.run(["say", "-o", str(aiff), phrase], capture_output=True)
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000",
                            "-c", "1", str(aiff), str(wav)], capture_output=True)
            result = subprocess.run([str(SPEECH), "transcribe", "--file", str(wav)],
                                    capture_output=True, text=True, timeout=200)
            try:
                payload = json.loads(result.stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                print(f"   [SKIP] {label:<16} helper unavailable "
                      f"(exit {result.returncode})")
                continue
            if not payload.get("ok"):
                print(f"   [SKIP] {label:<16} {payload.get('error', '')[:52]}")
                continue

            heard = payload.get("text", "")
            extracted = spoken_digits(heard)
            ok = extracted == "482913"
            passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
            mark = "ok  " if ok else "FAIL"
            print(f"   [{mark}] {label:<16} heard {heard!r} → {extracted!r}")
    return passed, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", action="store_true",
                        help="also speak codes aloud and transcribe them")
    args = parser.parse_args()

    print("Spoken TOTP codes — recognised, and never run as a task\n")
    passed, failed = string_cases()

    if args.audio:
        audio_passed, audio_failed = audio_cases()
        passed += audio_passed
        failed += audio_failed
    else:
        print("\n   (run with --audio, from Terminal, to check what the "
              "recogniser\n    actually emits — that needs Speech Recognition)")

    print("\n" + "─" * 70)
    print(f"  {passed} passed, {failed} failed")
    if failed:
        print("\n  A failure here is a security bug, not a cosmetic one:")
        print("  an unrecognised code is forwarded to an engine as task text.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

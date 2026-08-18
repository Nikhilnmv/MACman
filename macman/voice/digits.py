"""Turn a spoken number into digits.

A TOTP code typed into Messages arrives as `482913`. The same code *spoken*
does not: a recogniser may return `482913`, `482 913`, `four eight two nine one
three`, or `forty-eight twenty-nine thirteen`, depending on how the speaker
grouped it.

This matters for security, not convenience. `security.auth.verify` reduces a
code to its digits, so a worded code reduces to nothing and fails. Worse,
`session._looks_like_code` counts digits to decide whether a message is *only*
a code — and a worded code contains none, so it is classified as a task and
handed to an engine as text. That is the credential leak already fixed once for
iMessage, arriving again by another road.

So the rule here is the same as there, and deliberately strict:

    A code is recognised only when the entire utterance is nothing but a
    number. Anything else is a task.

`delete file 123456` must stay a task. `four eight two nine one three` must
become a code. There is no partial credit — a wrong guess in either direction
is a bug with a security consequence.
"""

from __future__ import annotations

import re

#: Single digits, including the two ways people say zero out loud.
_UNITS = {
    "zero": 0, "oh": 0, "o": 0, "nought": 0, "naught": 0,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
}

#: Ten to nineteen, which are single words and two digits.
_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}

#: Twenty to ninety, which may stand alone or lead a unit: "forty eight".
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

#: Filler that carries no digits and appears when people read codes aloud.
_IGNORED = {"my", "code", "is", "the", "it's", "its", "and", "dash", "space"}


def _tokens(text: str) -> list[str]:
    """Split into words and digit runs, treating hyphens as separators.

    `forty-eight` is one written word and two spoken ones, and a recogniser
    may emit either form.
    """
    return re.findall(r"[a-z']+|\d+", text.lower().replace("-", " "))


def spoken_digits(text: str, *, length: int = 6) -> str | None:
    """Extract a code of exactly `length` digits, or None.

    Returns None whenever the utterance contains anything that is not part of
    a number, so an ordinary request can never be mistaken for a credential.

    Args:
        text: One transcribed utterance.
        length: Digits a valid code must have. Six for TOTP.
    """
    if not text or not text.strip():
        return None

    tokens = [t for t in _tokens(text) if t not in _IGNORED]

    digits: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]

        if token.isdigit():
            digits.append(token)
        elif token in _UNITS:
            digits.append(str(_UNITS[token]))
        elif token in _TEENS:
            digits.append(str(_TEENS[token]))
        elif token in _TENS:
            # "forty eight" is one number, 48 — not 40 followed by 8. Without
            # this, a code grouped in pairs parses to eight digits and is
            # rejected, which sends the credential on to the engine as a task.
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            if following in _UNITS and _UNITS[following] != 0:
                digits.append(str(_TENS[token] + _UNITS[following]))
                index += 2
                continue
            digits.append(str(_TENS[token]))
        else:
            # A word that is not part of a number means this is speech, not a
            # code. Refusing here is what keeps "delete file 123456" a task.
            return None

        index += 1

    combined = "".join(digits)
    return combined if len(combined) == length else None


def looks_like_spoken_code(text: str, *, length: int = 6) -> bool:
    """Whether an utterance is nothing but a code.

    The spoken counterpart of `session._looks_like_code`, and used for the same
    purpose: suppressing a code so it is never forwarded to an engine as a task.
    """
    return spoken_digits(text, length=length) is not None

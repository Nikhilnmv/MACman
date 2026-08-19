"""The single point where data leaves this Mac.

MACman's central claim is that your files stay on your machine. That claim is
only as good as the number of places able to break it, so there is exactly one:
everything bound for a cloud model is described here, authorised here, and
recorded here.

## What a user is owed before data leaves

Not a policy document — a sentence they can read in the moment:

* **Where it goes.** Anthropic's API, or the `claude` CLI on their own machine.
* **What exactly goes.** The request, and the files, with sizes.
* **What does not.** Naming the untouched things is more reassuring than
  listing the touched ones, and unlike a promise it can be checked.
* **What it costs**, and to which account.

## Two kinds of disclosure, because there are two kinds of truth

The distinction matters enough to be in the type system:

* `EXACT` — MACman assembled the payload and can name every byte. This is the
  API path.
* `SCOPE` — MACman hands a task to `claude`, which then **reads files on its
  own**. MACman knows the folder it may read; it cannot know what it will read.

Rendering a `SCOPE` disclosure as though it were `EXACT` would be a lie of
precision: a tidy list of three files, when the real answer is "anything in
this project". A user deciding whether to trust this is entitled to the
difference.

## Fails closed

No authorisation, no send. A timeout refuses, an unparseable answer refuses,
and a missing `ask` callable refuses. `send` demands a receipt that only
`authorise` issues, so forgetting to ask is a `TypeError` rather than a silent
disclosure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

from macman.security.audit import AuditLog
from macman.security.paths import within as path_within


class EgressRefused(RuntimeError):
    """Raised when data would leave without authorisation."""


class Destination(str, Enum):
    ANTHROPIC_API = "anthropic_api"
    CLAUDE_CLI = "claude_cli"

    @property
    def label(self) -> str:
        return {
            Destination.ANTHROPIC_API: "Anthropic's API",
            Destination.CLAUDE_CLI: "Claude Code, on this Mac",
        }[self]


class Precision(str, Enum):
    """Whether the payload is known exactly, or only bounded."""

    EXACT = "exact"
    SCOPE = "scope"


@dataclass(frozen=True)
class PayloadItem:
    """One thing that leaves, described the way a person would describe it."""

    label: str
    #: "142 lines", "38 words". Human units; bytes mean nothing to a reader.
    detail: str = ""
    preview: str = ""

    def render(self) -> str:
        return f"{self.label} ({self.detail})" if self.detail else self.label


@dataclass(frozen=True)
class Disclosure:
    """Everything a user needs to decide, in one object.

    Deliberately plain data. The same disclosure is rendered as a native
    dialog, a text message, a spoken sentence and an audit record; building it
    four times is how the four fall out of step.
    """

    destination: Destination
    precision: Precision
    #: The request in the user's own words.
    reason: str
    #: What leaves. For SCOPE, what *may* leave.
    payload: tuple[PayloadItem, ...] = ()
    #: Named as untouched. Checkable, unlike a promise. Only put things here
    #: that are genuinely guaranteed — an exclusion that turns out to be
    #: aspirational is worse than saying nothing.
    excluded: tuple[str, ...] = ()
    #: Something the user is worse off for not knowing. Rendered separately
    #: from `excluded`, because a caveat filed under "not included" reads as
    #: reassurance and is the opposite.
    warning: str = ""
    billing: str = ""
    #: Matched against pre-approvals; None means no path is involved.
    path: Path | None = None
    #: Coarse kind, e.g. "coding". Pre-approvals are per category.
    category: str = "general"

    def headline(self) -> str:
        """The first line, and the one most likely to be the only line read.

        Destination-aware because the two senders are honest about different
        things. A single generic sentence would overstate one of them, and the
        overstatement would always be in the reassuring direction.
        """
        if self.precision is Precision.EXACT:
            return f"This sends data to {self.destination.label}."
        if self.destination is Destination.CLAUDE_CLI:
            return (f"This hands the task to {self.destination.label}, which "
                    f"will read files on its own.")
        return (f"This sends your request to {self.destination.label}. "
                f"Whatever Claude then looks up is sent too.")

    def as_lines(self) -> list[str]:
        """Rendered for a dialog: short lines, most important first."""
        lines = [self.headline(), ""]
        lines.append(f"Request: {self.reason.strip()[:200]}")

        if self.payload:
            lines.append("")
            lines.append(self._payload_heading())
            lines.extend(f"  • {item.render()}" for item in self.payload)

        if self.excluded:
            lines.append("")
            lines.append(f"Not included: {', '.join(self.excluded)}")

        if self.warning:
            lines.append("")
            lines.append(f"⚠ {self.warning}")

        if self.billing:
            lines.append("")
            lines.append(f"Cost: {self.billing}")
        return lines

    def _payload_heading(self) -> str:
        """Heading over the payload list.

        Three cases rather than two. "May read anything in" is right for a
        folder handed to Claude Code, and wrong for an API request, where the
        request itself goes exactly and only what follows is open-ended.
        Using one heading for both mislabels whichever it was not written for.
        """
        if self.precision is Precision.EXACT:
            return "Sends exactly:"
        if self.destination is Destination.CLAUDE_CLI:
            return "May read anything in:"
        return "Sends:"

    def as_text(self) -> str:
        """Rendered for a message or spoken aloud."""
        return "\n".join(self.as_lines())

    def summary(self) -> str:
        """One line, for the audit log and the activity view."""
        what = ", ".join(item.label for item in self.payload) or "the request"
        return f"{self.destination.value}: {what}"


# --------------------------------------------------------------------------- #
# Pre-approvals
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PreApproval:
    """Standing permission for one narrow kind of task.

    Narrow on purpose: a category *and* a path, never a blanket allow. The
    point of asking every time is defeated by one checkbox that covers
    everything, so this cannot express that.

    Expiry is not decoration. Consent granted once and never revisited becomes
    a setting nobody remembers choosing, which is the failure mode this whole
    design exists to avoid.
    """

    category: str
    path_prefix: Path
    expires_at: float

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at

    def covers(self, disclosure: Disclosure, now: float | None = None) -> bool:
        if self.expired(now):
            return False
        if disclosure.category != self.category:
            return False
        if disclosure.path is None:
            # A pre-approval is scoped to a directory; a task with no path is
            # outside every scope, so it always asks.
            return False
        return path_within(disclosure.path.expanduser().resolve(),
                           self.path_prefix.expanduser().resolve())

    def describe(self) -> str:
        """One line for the settings UI, in the user's terms."""
        remaining = max(0, int((self.expires_at - time.time()) // 86_400))
        return (f"{self.category} tasks under {self.path_prefix} "
                f"— {remaining} day(s) left")


def load_pre_approvals() -> tuple[PreApproval, ...]:
    """Read standing permissions from the user's config.

    Absent or malformed entries yield *nothing* rather than a default rule.
    A parsing bug must not be able to invent consent, so every failure here
    means "ask the owner".
    """
    from macman import userconfig

    # Read fresh rather than cached: revoking a pre-approval in Settings has
    # to take effect on the next task, not the next restart.
    raw = userconfig.load().get("cloud_preapprovals") or []
    rules: list[PreApproval] = []
    for entry in raw:
        try:
            rules.append(PreApproval(
                category=str(entry["category"]),
                path_prefix=Path(str(entry["path"])).expanduser(),
                expires_at=float(entry["expires_at"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(rules)


# --------------------------------------------------------------------------- #
# Authorisation
# --------------------------------------------------------------------------- #

#: Asks the owner. Signature matches the existing `confirm` callback, so
#: `channels.confirm.TextConfirmer` works unchanged — it already fails closed
#: on a timeout and on any answer that is not an explicit yes.
Asker = Callable[[str, str], bool]


@dataclass(frozen=True)
class Authorisation:
    """Proof that a specific disclosure was approved.

    `send` requires one, so omitting the question is a `TypeError` at the call
    site rather than data quietly leaving. Structural, not a convention.
    """

    disclosure: Disclosure
    granted_at: float
    #: "owner" when asked, "pre-approved" when a standing rule matched.
    basis: str


def authorise(
    disclosure: Disclosure,
    *,
    ask: Asker | None,
    audit: AuditLog,
    session_id: str,
    pre_approvals: Iterable[PreApproval] = (),
) -> Authorisation:
    """Get permission for one disclosure, or raise.

    Order matters: pre-approvals are checked first so routine work does not
    interrupt, but a matching rule is still **recorded**. Silent must never
    mean invisible — the activity view shows auto-approved sends alongside
    asked ones.

    Raises:
        EgressRefused: if the owner declines, cannot be reached, or no `ask`
            was supplied. Every path out of here that is not an approval is a
            refusal.
    """
    now = time.time()

    for rule in pre_approvals:
        if rule.covers(disclosure, now):
            audit.security(
                event="egress_pre_approved", session=session_id,
                destination=disclosure.destination.value,
                category=disclosure.category,
                scope=str(rule.path_prefix),
                summary=disclosure.summary(),
            )
            return Authorisation(disclosure, now, basis="pre-approved")

    if ask is None:
        audit.security(
            event="egress_refused", session=session_id,
            destination=disclosure.destination.value,
            reason="nobody to ask", summary=disclosure.summary(),
        )
        raise EgressRefused(
            "Nothing was sent: this needs your approval and there is no way to "
            "ask you right now."
        )

    approved = ask(
        f"sends data to {disclosure.destination.label}",
        disclosure.as_text(),
    )

    audit.security(
        event="egress_approved" if approved else "egress_refused",
        session=session_id,
        destination=disclosure.destination.value,
        category=disclosure.category,
        summary=disclosure.summary(),
    )

    if not approved:
        raise EgressRefused("Refused by the owner — nothing was sent.")
    return Authorisation(disclosure, now, basis="owner")


def guard(authorisation: Authorisation, disclosure: Disclosure) -> None:
    """Check a receipt matches what is about to be sent, immediately before sending.

    Stops the payload being swapped after approval — whether by a bug that
    rebuilds the request, or by anything that has convinced the agent to
    reuse an old receipt for new data. Approving *this* is not approving
    *something like it*.
    """
    if authorisation.disclosure is not disclosure:
        raise EgressRefused(
            "Refusing to send: this is not the data that was approved."
        )

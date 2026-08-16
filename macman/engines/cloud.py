"""Cloud engine — the developer task set, on Claude.

Wraps the SDK's Tool Runner, which owns the agentic loop. MACman's own
enforcement lives in `agent/tools/registry.py` rather than here, so this module
is deliberately thin: it assembles the request, iterates the loop, and accounts
for what it cost.

Only reached for tasks the router sends here. Anything private never gets this
far — see `macman/router.py`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

import anthropic

from macman import config
from macman.agent import prompts
from macman.agent.tools.registry import ALL_TOOLS, ToolContext, set_context
from macman.security import lockstate
from macman.security.audit import AuditLog

#: USD per million tokens for `claude-opus-5`. Cache writes cost 1.25x input,
#: cache reads 0.1x — the reason the stable prompt carries a breakpoint.
_INPUT_PER_MTOK = 5.00
_OUTPUT_PER_MTOK = 25.00
_CACHE_WRITE_PER_MTOK = 6.25
_CACHE_READ_PER_MTOK = 0.50

#: Caps thinking *and* text together on Opus 5, so it needs headroom above what
#: the visible reply alone would need.
MAX_TOKENS = 16_000

#: Stops a pathological loop from spending unbounded money.
MAX_ITERATIONS = 30


@dataclass
class Cost:
    """Token accounting for one task. Estimates in DESIGN.md §11 are replaced
    by these numbers once real sessions have run."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def usd(self) -> float:
        return (
            self.input_tokens / 1e6 * _INPUT_PER_MTOK
            + self.output_tokens / 1e6 * _OUTPUT_PER_MTOK
            + self.cache_write_tokens / 1e6 * _CACHE_WRITE_PER_MTOK
            + self.cache_read_tokens / 1e6 * _CACHE_READ_PER_MTOK
        )

    def add(self, usage: object) -> None:
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0

    def summary(self) -> str:
        return (
            f"${self.usd:.4f} "
            f"(in {self.input_tokens}, out {self.output_tokens}, "
            f"cache w{self.cache_write_tokens}/r{self.cache_read_tokens})"
        )


@dataclass
class Outcome:
    text: str
    stop_reason: str | None
    turns: int
    cost: Cost
    refused: bool = False


def _default_confirm(reason: str, summary: str) -> bool:
    """Terminal confirmation, used until the channels exist.

    Defaults to *no* on an unreadable answer: an unattended MACman must not
    approve a destructive action by accident.
    """
    print(f"\n  ⚠️  MACman wants to run something that {reason}:\n     {summary}")
    return input("     Allow? [y/N] ").strip().lower() in {"y", "yes"}


@dataclass
class CloudEngine:
    audit: AuditLog = field(default_factory=AuditLog)
    client: anthropic.Anthropic = field(default_factory=anthropic.Anthropic)

    def __post_init__(self) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set — the cloud engine cannot run. "
                "Private tasks are unaffected; they never reach this engine."
            )

    def run(
        self,
        task: str,
        *,
        session_id: str,
        confirm: Callable[[str, str], bool] | None = None,
        history: list | None = None,
    ) -> Outcome:
        """Execute one task and return the final reply.

        Args:
            task: The owner's request, verbatim.
            session_id: Correlates every audit record for this session.
            confirm: Asks the owner to approve a guarded action.
            history: Prior messages, for multi-turn conversations.
        """
        state = lockstate.read()

        set_context(ToolContext(
            session_id=session_id,
            engine="cloud",
            audit=self.audit,
            confirm=confirm or _default_confirm,
        ))

        self.audit.session(
            session_id=session_id, event="task_start", engine="cloud",
            tier=state.tier.value, task=task[:400],
        )

        messages = list(history or []) + [{"role": "user", "content": task}]
        cost = Cost()

        runner = self.client.beta.messages.tool_runner(
            model=config.CLOUD_MODEL,
            max_tokens=MAX_TOKENS,
            max_iterations=MAX_ITERATIONS,
            system=[
                # Cache breakpoint sits on the stable half; the session half
                # follows it and changes freely without invalidating the cache.
                {
                    "type": "text",
                    "text": prompts.STABLE_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": prompts.session_system(state)},
            ],
            tools=ALL_TOOLS,
            messages=messages,
        )

        final = None
        turns = 0
        for message in runner:
            turns += 1
            final = message
            if usage := getattr(message, "usage", None):
                cost.add(usage)

        text = ""
        stop_reason = None
        refused = False

        if final is not None:
            stop_reason = getattr(final, "stop_reason", None)
            # Check refusal before reading content: on a refusal the content
            # blocks are not the model's answer.
            if stop_reason == "refusal":
                refused = True
                text = (
                    "Claude declined to continue with this request. "
                    "Rephrasing usually helps; if not, it may need doing by hand."
                )
            else:
                text = "\n".join(
                    block.text for block in getattr(final, "content", [])
                    if getattr(block, "type", None) == "text"
                ).strip()

        self.audit.session(
            session_id=session_id, event="task_end", engine="cloud",
            turns=turns, stop_reason=stop_reason, cost_usd=round(cost.usd, 6),
        )

        return Outcome(text=text, stop_reason=stop_reason, turns=turns,
                       cost=cost, refused=refused)

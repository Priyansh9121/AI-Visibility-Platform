"""A ceiling on every paid Anthropic call outside the engine adapters.

`engines.py` learned the shape the expensive way (Epic 9.2): the SDK's
`timeout` bounds one HTTP ATTEMPT, its retry policy retries timeouts, and the
pinned anthropic 0.125.0 defaults to two retries on a 600-second read timeout
— so a call that "has a timeout" can run for thirty minutes and bill three
generations. Its answer was three explicit numbers and an outer
`asyncio.timeout` that makes the arithmetic a guarantee rather than a claim
about someone else's internals.

Five other call sites made the same paid call with none of that (API key
discipline audit, 2026-09-07): sentiment in `extraction.py`, classification
in `classify.py`, co-citation discovery in `cocitation.py`, prompt generation
in `prompts.py`, and fix generation in `fix_generator.py` — which bounded the
attempt and not the retries, and said so in a comment. This module is the
engine adapters' shape as a VALUE, so a call site declares one `CallBound`
and inherits nothing. The SDK default is not ours to assume, whichever site
is asking.

It also exists for the lease (build log, the same audit, "The spend
ceiling"). A lease length is the longest gap between two renewals plus a
margin, and that gap is a sum of ceilings. Ceilings that live in one shape
can be read and added; ceilings that live in five modules' worth of prose
cannot.

`engines.py` deliberately keeps its own three constants rather than becoming
a `CallBound`: `test_engine_timeout.py` pins them by name and swaps them by
value, and rewriting a verified fix for symmetry is how a verified fix stops
being one. `test_call_bounds.py` asserts the two arithmetics agree instead.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import anthropic

from ..config import Settings

# Between attempts the SDK sleeps INITIAL_RETRY_DELAY (0.5s) doubling toward
# MAX_RETRY_DELAY (8.0s), times a jitter in (0.75, 1.0]. That is at most 0.5s
# for one retry and 1.5s for two; 2.0s covers both with room, and is the same
# allowance `engines.py` chose. `test_call_bounds.py` reads the SDK's own
# constants and asserts every declared bound is still covered, so an SDK bump
# that changes the schedule fails a test rather than an arithmetic claim.
RETRY_BACKOFF_ALLOWANCE = 2.0


@dataclass(frozen=True, slots=True)
class CallBound:
    """How long one call may take, every attempt included.

    `timeout` and `max_retries` go to the client verbatim: one bounds an
    attempt, the other counts them. `ceiling` is the whole call, and
    `deadline()` is what enforces it — an outer `asyncio.timeout` that holds
    even if the SDK's retry or backoff internals move under the version range
    pyproject allows (`anthropic>=0.40,<1`).
    """

    timeout: float
    max_retries: int
    backoff_allowance: float = RETRY_BACKOFF_ALLOWANCE

    @property
    def ceiling(self) -> float:
        """Seconds the whole call may take: attempts x per-attempt bound, plus backoff."""
        return self.timeout * (self.max_retries + 1) + self.backoff_allowance

    def client(self, settings: Settings) -> anthropic.AsyncAnthropic:
        """An Anthropic client that inherits neither number from the SDK."""
        return anthropic.AsyncAnthropic(
            api_key=settings.provider_key("anthropic_api_key"),
            timeout=self.timeout,
            max_retries=self.max_retries,
        )

    def deadline(self) -> asyncio.Timeout:
        """The enforced ceiling: `async with bound.deadline(): await ...`.

        Expiry raises the builtin `TimeoutError`, which is NOT an
        `anthropic.APIError` — a call site that catches only the SDK's
        exceptions lets it escape. Every site catches it alongside
        `anthropic.APITimeoutError`, and BEFORE `APIConnectionError`, which
        that class subclasses: checked in the wrong order, the connection
        branch swallows every timeout and reports an unreachable provider,
        exactly as `_map_error` in `engines.py` records happening to it.
        """
        return asyncio.timeout(self.ceiling)

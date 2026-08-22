"""Engine adapters — §5.4 step 4, "run each prompt against each AI engine".

=============================================================================
IP-SAFETY BOUNDARY (ip-safety.md #7)
=============================================================================
`EngineAnswer.text` holds a live engine response. It is a plain dataclass with
no SQLAlchemy mapping and no persistence path — the same contract as
`CrawlResult` (Epic 2) and `SerpResult` (Epic 3). It exists so
`extraction.py` can derive facts in the same request, and is discarded with it.

What survives is on EngineResult: booleans, ordinal positions, a sentiment
label, cited domains and URLs, and a SHA-256 digest of the answer.
`response_digest` is how re-scan change-detection works *without* keeping the
text — two scans can be compared for "did the answer change?" by comparing
hashes.
=============================================================================

Adding an engine
----------------
Implement `EngineAdapter` and register it in `ENGINE_REGISTRY`. Nothing in the
runner, extraction or persistence layer knows which engines exist. Adding
ChatGPT or Perplexity is a new class plus a provider key — no pipeline change.

Why two Claude modes rather than two vendors
--------------------------------------------
Only ANTHROPIC_API_KEY is provisioned. The two adapters below are genuinely
different answer engines in the way this product measures:

* `claude` answers from parametric recall — it names what it learned in
  training, and cites nothing. This is how a non-browsing assistant behaves.
* `claude_search` retrieves live and answers with real cited URLs. This is how
  a grounded assistant (Perplexity, AI Overviews) behaves, and it is the only
  source of genuine Citation rows available today.

They routinely disagree, which is the point — a brand can be absent from
grounded answers while present in parametric ones, and that gap is exactly what
a report should surface. **They are still one vendor and one model**, so this
does not test cross-vendor variance. That limitation is credential-bound, not
design-bound.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Protocol

import anthropic
import structlog

from ..config import Settings, get_settings
from ..models.engine_result import Engine, EngineResultStatus
from .crawl import registrable_domain

logger = structlog.get_logger(__name__)

ANSWER_MODEL = "claude-opus-5"
ANSWER_EFFORT = "low"
ANSWER_MAX_TOKENS = 4_000
SEARCH_MAX_USES = 4
DEFAULT_TIMEOUT = 90.0


@dataclass(slots=True)
class CitedSource:
    """A source an engine cited. Facts only — no title, no snippet."""

    url: str
    domain: str
    position: int


@dataclass(slots=True)
class EngineAnswer:
    """One engine's response. **Transient — never persist `text`.**"""

    engine: Engine
    engine_version: str
    prompt_text: str
    text: str = ""
    citations: list[CitedSource] = field(default_factory=list)
    status: EngineResultStatus = EngineResultStatus.OK
    error_code: str | None = None
    latency_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status in (EngineResultStatus.OK, EngineResultStatus.ANSWERED_NO_MENTION)

    def digest(self) -> str | None:
        """SHA-256 of the answer text.

        Lets a re-scan detect "the answer changed" without retaining the answer.
        Normalised on whitespace first, so trivial reformatting does not read as
        a substantive change.
        """
        if not self.text:
            return None
        normalised = " ".join(self.text.split())
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()

    def redacted(self) -> dict[str, object]:
        """Log-safe view — never includes answer text."""
        return {
            "engine": self.engine.value,
            "status": self.status.value,
            "citations": len(self.citations),
            "chars": len(self.text),
            "latency_ms": self.latency_ms,
            "error_code": self.error_code,
        }


class EngineAdapter(Protocol):
    """The contract every engine implements."""

    engine: Engine
    version: str

    async def ask(self, prompt: str, *, settings: Settings) -> EngineAnswer: ...


def _map_error(exc: Exception) -> tuple[EngineResultStatus, str]:
    if isinstance(exc, anthropic.RateLimitError):
        return EngineResultStatus.RATE_LIMITED, "PROVIDER_RATE_LIMITED"
    if isinstance(exc, anthropic.AuthenticationError):
        return EngineResultStatus.ERROR, "PROVIDER_AUTH_FAILED"
    if isinstance(exc, anthropic.BadRequestError):
        if "credit balance" in str(exc).lower():
            return EngineResultStatus.ERROR, "PROVIDER_QUOTA_EXHAUSTED"
        return EngineResultStatus.ERROR, "PROVIDER_BAD_REQUEST"
    if isinstance(exc, anthropic.APIConnectionError):
        return EngineResultStatus.ERROR, "PROVIDER_UNREACHABLE"
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return EngineResultStatus.TIMEOUT, "TIMEOUT"
    return EngineResultStatus.ERROR, "PROVIDER_ERROR"


class _ClaudeBase:
    """Shared plumbing for both Claude adapters."""

    engine: Engine
    version: str
    uses_search: bool = False

    async def ask(self, prompt: str, *, settings: Settings) -> EngineAnswer:
        client = anthropic.AsyncAnthropic(
            api_key=settings.provider_key("anthropic_api_key"), timeout=DEFAULT_TIMEOUT
        )
        answer = EngineAnswer(
            engine=self.engine, engine_version=self.version, prompt_text=prompt
        )
        started = time.perf_counter()

        kwargs: dict = {
            "model": ANSWER_MODEL,
            "max_tokens": ANSWER_MAX_TOKENS,
            "output_config": {"effort": ANSWER_EFFORT},
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.uses_search:
            kwargs["tools"] = [
                {
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "max_uses": SEARCH_MAX_USES,
                }
            ]

        try:
            response = await client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to a status, never raised
            answer.status, answer.error_code = _map_error(exc)
            answer.latency_ms = int((time.perf_counter() - started) * 1000)
            return answer

        answer.latency_ms = int((time.perf_counter() - started) * 1000)

        if response.stop_reason == "refusal":
            answer.status = EngineResultStatus.ERROR
            answer.error_code = "PROVIDER_REFUSED"
            return answer

        answer.text = "".join(b.text for b in response.content if b.type == "text")
        answer.citations = _extract_citations(response)
        return answer


def _extract_citations(response) -> list[CitedSource]:  # noqa: ANN001
    """Pull cited URLs out of a response.

    Reads the structured `web_search_tool_result` blocks rather than parsing
    URLs out of prose: the blocks are already facts, and regexing the answer
    text would pick up whatever the model happened to type.

    **Titles and page snippets in those blocks are deliberately ignored.** They
    are publisher copy (ip-safety.md #7). Only the URL and its registrable
    domain are kept.
    """
    citations: list[CitedSource] = []
    seen: set[str] = set()

    for block in response.content:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        content = getattr(block, "content", None)
        # An error result is a single object, not a list — branch before
        # indexing, or an upstream search failure raises a TypeError here.
        if not isinstance(content, list):
            continue
        for item in content:
            url = getattr(item, "url", None)
            if not url or url in seen:
                continue
            domain = registrable_domain(url)
            if not domain:
                continue
            seen.add(url)
            citations.append(
                CitedSource(url=url[:2048], domain=domain, position=len(citations) + 1)
            )
    return citations


class ClaudeParametricAdapter(_ClaudeBase):
    """Answers from training only. No browsing, no citations."""

    engine = Engine.CLAUDE
    version = f"{ANSWER_MODEL}/parametric"
    uses_search = False


class ClaudeSearchAdapter(_ClaudeBase):
    """Answers with live retrieval. Produces real cited URLs."""

    engine = Engine.CLAUDE_SEARCH
    version = f"{ANSWER_MODEL}/web_search_20260209"
    uses_search = True


ENGINE_REGISTRY: dict[Engine, EngineAdapter] = {
    Engine.CLAUDE: ClaudeParametricAdapter(),
    Engine.CLAUDE_SEARCH: ClaudeSearchAdapter(),
}

DEFAULT_ENGINES: tuple[Engine, ...] = (Engine.CLAUDE, Engine.CLAUDE_SEARCH)


async def ask_all(
    prompt: str,
    *,
    engines: tuple[Engine, ...] = DEFAULT_ENGINES,
    settings: Settings | None = None,
) -> list[EngineAnswer]:
    """Ask one prompt of every engine, concurrently."""
    settings = settings or get_settings()
    return list(
        await asyncio.gather(
            *(ENGINE_REGISTRY[e].ask(prompt, settings=settings) for e in engines)
        )
    )

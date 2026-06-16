"""LLM port: run one agentic turn (the model may call tools) and return the text.

The core depends on this Protocol, not on any SDK. The Claude Agent SDK adapter
lives in ../adapters/anthropic_agent.py; swapping engines means a new adapter,
not a core change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AgentResult:
    """Outcome of one agent turn."""

    text: str                    # final human-facing text (→ prose split downstream)
    cost_usd: float | None = None
    usage: dict | None = None


class LLMError(Exception):
    """An agent turn failed. ``code`` carries the SDK's classification when known
    (e.g. 'rate_limit', 'authentication_failed', 'billing_error') so the caller
    can decide whether to retry."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code

    @property
    def retryable(self) -> bool:
        return self.code in {"rate_limit", "server_error"}


class LLMPort(Protocol):
    async def run_turn(
        self, prompt: str, *, image: tuple[bytes, str] | None = None
    ) -> AgentResult: ...
    # ``image`` = (raw bytes, media_type); chat passes a photo, reports never do.
    # Optional + keyword-only, so the report path (run_turn(prompt)) is unchanged.

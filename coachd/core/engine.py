"""CoachEngine — the report turn, composed from the parts already built.

It invents nothing: it wires the prompt builder, the LLM port, the retry +
anti-empty guard, and the journal into one flow, mirroring coach.sh:

    tail journal → build prompt → run agent (retry while EMPTY) →
        OK    → record metrics + return header+prose
        EMPTY → "not synced yet" notice, record NOTHING
        ERROR → failure notice

The agent passed in is already configured with the provider's read tools and the
methodology system prompt (composition happens at the entrypoint); the engine
only supplies the per-turn user prompt. Reports are read-only — no write-guard
needed here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Awaitable, Callable

from ..ports.llm import AgentResult, LLMError, LLMPort
from .journal import Journal
from .prompts import build_report_prompt
from .resilience import RetryPolicy, RunState, run_with_retry_async

_HEADERS = {"morning": "🌅 Garmin ранок", "evening": "🌙 Garmin вечір"}


@dataclass(frozen=True)
class ReportOutcome:
    state: RunState
    message: str            # text to deliver to Telegram (header + prose, or a notice)
    prose: str | None       # the recorded prose (OK only)
    cost_usd: float | None  # agent cost of the successful/last turn, when known


class CoachEngine:
    def __init__(
        self,
        *,
        llm: LLMPort,
        journal: Journal,
        user_name: str,
        worn_start: date,
        policy: RetryPolicy = RetryPolicy(),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._llm = llm
        self._journal = journal
        self._user_name = user_name
        self._worn_start = worn_start
        self._policy = policy
        self._sleep = sleep

    async def run_report(self, mode: str, on_date: date, now_str: str) -> ReportOutcome:
        prompt = build_report_prompt(
            mode,
            on_date,
            now_str,
            self._journal.tail(14),
            user_name=self._user_name,
            worn_start=self._worn_start,
        )

        last_result: dict[str, AgentResult | None] = {"r": None}

        async def _run() -> tuple[int, str]:
            try:
                result = await self._llm.run_turn(prompt)
            except LLMError:
                last_result["r"] = None
                return 1, ""  # ERROR — classified by the guard
            last_result["r"] = result
            return 0, result.text

        state, raw = await run_with_retry_async(
            _run,
            lambda out: self._journal.probe(mode, out),
            self._policy,
            sleep=self._sleep,
        )

        header = f"{_HEADERS.get(mode, 'Garmin')} — {on_date.isoformat()}"
        cost = last_result["r"].cost_usd if last_result["r"] else None

        if state is RunState.OK:
            prose = self._journal.record(on_date.isoformat(), mode, raw)
            return ReportOutcome(RunState.OK, f"{header}\n\n{prose}", prose, cost)

        if state is RunState.EMPTY:
            # do NOT write a row of nulls — tell the user to sync and retry
            notice = (
                f"{header}\n\nСвіжі дані з годинника ще не синхнулись у Garmin Connect "
                f"({self._policy.max_tries} спроб). Звіт пропущено — синхронізуй "
                f"годинник і запусти ще раз."
            )
            return ReportOutcome(RunState.EMPTY, notice, None, cost)

        notice = (
            f"⚠️ Garmin coach ({mode}, {on_date.isoformat()}): не вдалося отримати "
            f"аналіз. Спробуй пізніше."
        )
        return ReportOutcome(RunState.ERROR, notice, None, cost)

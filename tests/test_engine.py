"""CoachEngine report flow: OK records + delivers, EMPTY retries + skips, ERROR notice."""

from __future__ import annotations

import asyncio
from datetime import date

from coachd.core.engine import CoachEngine, ReportOutcome
from coachd.core.journal import Journal
from coachd.core.resilience import RetryPolicy, RunState
from coachd.ports.llm import AgentResult, LLMError

WORN = date(2026, 6, 8)
TODAY = date(2026, 6, 15)
NOW = "2026-06-15 07:00 EEST"

_VALID = (
    "Доброго ранку! Готовність висока.\n"
    "===METRICS===\n"
    '{"sleep_h":7.5,"rhr":48,"body_battery_charged":80,"verdict":"якісне ОК"}'
)
_EMPTY_CORE = (
    "Доброго ранку.\n"
    "===METRICS===\n"
    '{"sleep_h":null,"rhr":"","body_battery_charged":[]}'
)


class _FakeLLM:
    """Returns/raises a scripted item per run_turn call (for retry scenarios)."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    async def run_turn(self, prompt):
        item = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if isinstance(item, BaseException):
            raise item
        return item


def _engine(tmp_path, llm, *, tries=3):
    sleeps: list[float] = []

    async def _no_sleep(s):
        sleeps.append(s)

    eng = CoachEngine(
        llm=llm,
        journal=Journal(tmp_path / "journal.jsonl"),
        user_name="Віталій",
        worn_start=WORN,
        policy=RetryPolicy(max_tries=tries, retry_wait_s=240.0),
        sleep=_no_sleep,
    )
    return eng, sleeps


def test_ok_records_and_delivers(tmp_path):
    llm = _FakeLLM([AgentResult(text=_VALID, cost_usd=0.04)])
    eng, sleeps = _engine(tmp_path, llm)
    out: ReportOutcome = asyncio.run(eng.run_report("morning", TODAY, NOW))

    assert out.state is RunState.OK
    assert out.message.startswith("🌅 Garmin ранок — 2026-06-15")
    assert "Готовність висока" in out.message
    assert "===METRICS===" not in out.message  # metrics never leak to Telegram
    assert out.cost_usd == 0.04
    assert sleeps == []                          # OK first try → no retry wait
    # persisted exactly one record, flat metrics
    recs = Journal(tmp_path / "journal.jsonl").read_records()
    assert len(recs) == 1 and recs[0]["sleep_h"] == 7.5


def test_empty_retries_then_skips_without_recording(tmp_path):
    llm = _FakeLLM([AgentResult(text=_EMPTY_CORE, cost_usd=0.01)])
    eng, sleeps = _engine(tmp_path, llm)
    out = asyncio.run(eng.run_report("morning", TODAY, NOW))

    assert out.state is RunState.EMPTY
    assert "не синхнулись" in out.message
    assert out.prose is None
    assert llm.calls == 3                         # exhausted all tries
    assert sleeps == [240.0, 240.0]               # 3 tries → 2 waits
    # nothing written — no row of nulls
    assert Journal(tmp_path / "journal.jsonl").read_records() == []


def test_empty_then_ok_recovers(tmp_path):
    llm = _FakeLLM([
        AgentResult(text=_EMPTY_CORE, cost_usd=0.01),
        AgentResult(text=_VALID, cost_usd=0.05),
    ])
    eng, sleeps = _engine(tmp_path, llm)
    out = asyncio.run(eng.run_report("morning", TODAY, NOW))

    assert out.state is RunState.OK
    assert llm.calls == 2
    assert sleeps == [240.0]
    assert len(Journal(tmp_path / "journal.jsonl").read_records()) == 1


def test_error_returns_notice_and_records_nothing(tmp_path):
    llm = _FakeLLM([LLMError("boom", code="server_error")])
    eng, sleeps = _engine(tmp_path, llm)
    out = asyncio.run(eng.run_report("evening", TODAY, "2026-06-15 22:00 EEST"))

    assert out.state is RunState.ERROR
    assert "не вдалося" in out.message
    assert Journal(tmp_path / "journal.jsonl").read_records() == []

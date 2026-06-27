"""DailyDigest: the cross-agent daily memory that stops the evening report from
contradicting the chat coach. Pins the empty-day skip, the deterministic
confirmed-action feed, advice-only days, the one-line contract, and the
never-block-the-report failure mode."""

from __future__ import annotations

import asyncio
from datetime import date
from zoneinfo import ZoneInfo

from coachd.core.daily_digest import DailyDigest
from coachd.core.i18n import Strings
from coachd.core.journal import Journal
from coachd.core.pending import PendingStore
from coachd.core.session_store import SessionStore
from coachd.ports.llm import AgentResult, LLMError

KYIV = ZoneInfo("Europe/Kyiv")
DAY = date(2026, 6, 15)
# 07:00Z / 08:00Z → 10:00 / 11:00 Kyiv → the 15th locally
_UTC_MORNING = "2026-06-15T07:00:00+00:00"
_UTC_LATER = "2026-06-15T08:00:00+00:00"


class _FakeLLM:
    """Captures the prompt; returns a scripted AgentResult or raises."""

    def __init__(self, result):
        self._result = result
        self.prompts: list[str] = []

    async def run_turn(self, prompt, *, image=None):
        self.prompts.append(prompt)
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class _BoomLLM:
    async def run_turn(self, prompt, *, image=None):
        raise AssertionError("LLM must not be called on an empty day")


def _stores(tmp_path):
    pending = PendingStore(
        tmp_path / "pending.json", nonce_factory=lambda: "N1", now=lambda: _UTC_MORNING
    )
    sessions = SessionStore(tmp_path / "sessions.json", now=lambda: _UTC_LATER)
    journal = Journal(tmp_path / "journal.jsonl", now=lambda: "2026-06-15T22:00:00+03:00")
    return pending, sessions, journal


def _digest(llm, pending, sessions, journal):
    return DailyDigest(
        llm=llm, pending=pending, sessions=sessions, journal=journal,
        strings=Strings("en"), tz=KYIV,
    )


def test_empty_day_writes_no_row_and_skips_llm(tmp_path):
    pending, sessions, journal = _stores(tmp_path)
    out = asyncio.run(_digest(_BoomLLM(), pending, sessions, journal).run(DAY))
    assert out is None
    assert journal.read_records() == []


def test_confirmed_action_writes_interactions_row(tmp_path):
    pending, sessions, journal = _stores(tmp_path)
    a = pending.put("mcp__garmin__upload_workout", {"name": "Easy 5k"})
    pending.confirm(a.nonce)
    sessions.append(1, "user", "feeling a bit sore")
    llm = _FakeLLM(AgentResult(text="Coach scheduled Easy 5k today; user reported soreness"))

    out = asyncio.run(_digest(llm, pending, sessions, journal).run(DAY))

    assert out == "Coach scheduled Easy 5k today; user reported soreness"
    recs = [r for r in journal.read_records() if r["mode"] == "interactions"]
    assert len(recs) == 1
    assert recs[0]["date"] == "2026-06-15"
    assert recs[0]["verdict"] == out
    # the summarizer saw the confirmed action (ground truth) AND the conversation
    assert "upload_workout" in llm.prompts[0]
    assert "Easy 5k" in llm.prompts[0]
    assert "feeling a bit sore" in llm.prompts[0]


def test_advice_only_day_still_summarizes(tmp_path):
    pending, sessions, journal = _stores(tmp_path)
    sessions.append(1, "assistant", "rest today, you are fatigued")
    llm = _FakeLLM(AgentResult(text="Coach advised rest; no workout scheduled"))

    out = asyncio.run(_digest(llm, pending, sessions, journal).run(DAY))

    assert out == "Coach advised rest; no workout scheduled"
    assert any(r["mode"] == "interactions" for r in journal.read_records())


def test_llm_failure_skips_row_without_raising(tmp_path):
    pending, sessions, journal = _stores(tmp_path)
    sessions.append(1, "user", "anything")
    # a summarizer failure must NEVER block the report that consumes the row
    out = asyncio.run(
        _digest(_FakeLLM(LLMError("boom", code="server_error")), pending, sessions, journal).run(DAY)
    )
    assert out is None
    assert journal.read_records() == []


def test_summary_is_collapsed_to_one_line(tmp_path):
    pending, sessions, journal = _stores(tmp_path)
    sessions.append(1, "user", "x")
    llm = _FakeLLM(AgentResult(text="line one\nline two   with   spaces\n"))

    out = asyncio.run(_digest(llm, pending, sessions, journal).run(DAY))

    assert "\n" not in out                       # one-line contract for the tail
    assert out == "line one line two with spaces"


def test_digest_row_reaches_the_evening_report_prompt(tmp_path):
    # the whole point: a workout the chat coach scheduled becomes visible to the
    # evening report through the EXISTING journal tail, alongside the instruction
    # not to scold the user for following the coach's own advice.
    from coachd.core.prompts import build_report_prompt

    pending, sessions, journal = _stores(tmp_path)
    a = pending.put("mcp__garmin__upload_workout", {"name": "Easy 5k"})
    pending.confirm(a.nonce)
    sessions.append(1, "user", "ready to train")
    llm = _FakeLLM(AgentResult(text="Coach scheduled Easy 5k today"))
    asyncio.run(_digest(llm, pending, sessions, journal).run(DAY))

    prompt = build_report_prompt(
        "evening", DAY, "2026-06-15 22:00 EEST",
        journal.tail(14), user_name="Oleksa", worn_start=date(2026, 6, 8),
    )
    assert "Coach scheduled Easy 5k today" in prompt   # chat action now in the report context
    assert "do NOT fault the user" in prompt           # and the reconciliation instruction

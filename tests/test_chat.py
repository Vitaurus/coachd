"""ChatEngine: replies, history growth, and detection of parked write-actions."""

from __future__ import annotations

import asyncio

from coachd.core.chat import ChatEngine
from coachd.core.i18n import Strings
from coachd.core.pending import PendingStore
from coachd.core.session_store import SessionStore
from coachd.ports.llm import AgentResult, LLMError


class _ParkingAgent:
    """Fake chat agent. If `park` is set, simulates the write-guard parking a
    pending action during the turn (what can_use_tool would do)."""

    def __init__(self, pending, *, park=None, text="ось план", raise_exc=None):
        self._pending = pending
        self._park = park
        self._text = text
        self._raise = raise_exc

    async def run_turn(self, prompt):
        if self._raise:
            raise self._raise
        if self._park:
            self._pending.put(self._park, {"name": "intervals"})
        return AgentResult(text=self._text, cost_usd=0.01)


def _engine(tmp_path, agent):
    return ChatEngine(
        chat_agent=agent,
        sessions=SessionStore(tmp_path / "s.json", now=lambda: "t"),
        pending=PendingStore(tmp_path / "p.json", nonce_factory=lambda: "N1"),
        strings=Strings("uk"),
    )


def test_reply_and_history(tmp_path):
    pending = PendingStore(tmp_path / "p.json")
    eng = _engine(tmp_path, _ParkingAgent(pending, text="привіт!"))
    reply = asyncio.run(eng.run_chat(42, "як я сьогодні?"))
    assert reply.text == "привіт!"
    assert reply.pending == []
    # both turns recorded
    hist = eng._sessions.history(42)
    assert [t.role for t in hist] == ["user", "assistant"]
    assert hist[1].text == "привіт!"


def test_history_feeds_into_prompt(tmp_path):
    pending = PendingStore(tmp_path / "p.json")
    captured = {}

    class _Spy(_ParkingAgent):
        async def run_turn(self, prompt):
            captured["prompt"] = prompt
            return await super().run_turn(prompt)

    eng = _engine(tmp_path, _Spy(pending))
    asyncio.run(eng.run_chat(1, "перше"))
    asyncio.run(eng.run_chat(1, "друге"))
    assert "перше" in captured["prompt"]      # prior turn is in the next prompt
    assert "User: друге" in captured["prompt"]  # role label is English (prompt base)


def test_today_date_injected_into_prompt(tmp_path):
    # the model needs today's date to resolve "завтра"/"на середу" → schedule_date
    from datetime import datetime

    pending = PendingStore(tmp_path / "p.json")
    captured = {}

    class _Spy(_ParkingAgent):
        async def run_turn(self, prompt):
            captured["prompt"] = prompt
            return await super().run_turn(prompt)

    eng = ChatEngine(
        chat_agent=_Spy(pending),
        sessions=SessionStore(tmp_path / "s.json", now=lambda: "t"),
        pending=pending,
        strings=Strings("uk"),
        now=lambda: datetime(2026, 6, 15),  # a Monday
    )
    asyncio.run(eng.run_chat(1, "склади і заплануй на завтра"))
    assert "Today: 2026-06-15 (Monday)." in captured["prompt"]


def test_note_records_outcome_into_next_prompt(tmp_path):
    # a confirmed write's result, recorded via note(), is recalled next turn
    pending = PendingStore(tmp_path / "p.json")
    captured = {}

    class _Spy(_ParkingAgent):
        async def run_turn(self, prompt):
            captured["prompt"] = prompt
            return await super().run_turn(prompt)

    eng = _engine(tmp_path, _Spy(pending))
    eng.note(7, "✓ Created and scheduled for 2026-06-17.")
    asyncio.run(eng.run_chat(7, "коли в мене пробіжка?"))
    # the absolute date the coach actually committed is in the recalled history
    assert "2026-06-17" in captured["prompt"]
    assert "Coach: ✓ Created and scheduled for 2026-06-17." in captured["prompt"]


def test_parked_write_is_returned_for_confirmation(tmp_path):
    pending = PendingStore(tmp_path / "p.json", nonce_factory=lambda: "N1")
    eng = ChatEngine(
        chat_agent=_ParkingAgent(pending, park="mcp__garmin__upload_workout"),
        sessions=SessionStore(tmp_path / "s.json", now=lambda: "t"),
        pending=pending,
        strings=Strings("uk"),
    )
    reply = asyncio.run(eng.run_chat(1, "додай інтервали"))
    assert len(reply.pending) == 1
    assert reply.pending[0].tool == "mcp__garmin__upload_workout"
    assert reply.pending[0].input == {"name": "intervals"}


def test_llm_error_is_graceful(tmp_path):
    pending = PendingStore(tmp_path / "p.json")
    eng = _engine(tmp_path, _ParkingAgent(pending, raise_exc=LLMError("boom", code="server_error")))
    reply = asyncio.run(eng.run_chat(1, "питання"))
    assert "Не вдалося" in reply.text
    assert reply.pending == []
